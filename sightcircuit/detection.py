"""Detector interface, checksum-verified detectors and annotation.

``mobilenet-ssd`` is the device-validated baseline (Pascal VOC, 20 classes).
``yolox-nano`` is an 80-class COCO candidate under evaluation. Detectors keep
their native label names; ``sightcircuit.labels.canonical`` compares them.

Each ``DETECTORS`` class owns everything callers need to know about it: its
constructor takes the registry name, and the class methods ``model_files(name)``
and ``preprocessing(name)`` give its checksum-pinned assets and a structured
model-input description. The evaluator, smoke check and asset tests read those,
so adding a detector changes this module and its ``scripts/smoke-assets.json``
entries only.
"""
import math
import time
from typing import Protocol

from . import assets
from .frame import Detection, DetectionResult
from .labels import COCO_CLASSES
from .preprocess import letterbox

ASSETS = assets.ASSETS
MODEL_FILES = ("MobileNetSSD_deploy.prototxt", "MobileNetSSD_deploy.caffemodel")
CLASSES = (
    "background aeroplane bicycle bird boat bottle bus car cat chair cow "
    "diningtable dog horse motorbike person pottedplant sheep sofa train tvmonitor"
).split()
# The Python module whose version and native files identify each inference runtime.
RUNTIME_MODULES = {"opencv-dnn": "cv2", "onnxruntime": "onnxruntime"}


class Detector(Protocol):
    name: str
    classes: tuple
    runtime: str

    def detect(self, bgr, threshold: float) -> DetectionResult:
        """Detect objects in a nonempty uint8 BGR image; boxes use its pixel space."""


def verify_model():
    return assets.verify(MODEL_FILES)


def require_result(result):
    """Reject anything but a well-formed ``DetectionResult`` before it reaches output."""
    inference_ms = getattr(result, "inference_ms", None)
    if (not isinstance(result, DetectionResult) or type(result.detections) is not tuple or
            any(not isinstance(item, Detection) for item in result.detections) or
            isinstance(inference_ms, bool) or not isinstance(inference_ms, (int, float)) or
            not math.isfinite(inference_ms) or inference_ms < 0):
        raise ValueError("invalid_detection_result")
    return result


def validate_input(bgr, threshold):
    import numpy as np

    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Threshold must be between zero and one")
    if bgr.dtype != np.uint8 or bgr.ndim != 3 or bgr.shape[2] != 3 or not bgr.size:
        raise ValueError("Detector needs a nonempty uint8 BGR image")


def decode_ssd(prediction, width, height, threshold):
    """SSD ``DetectionOutput`` rows to detections in ``width`` × ``height`` input pixels.

    Rows are ``[image, class, score, x1, y1, x2, y2]`` with normalized corners.
    Non-finite rows, scores below ``threshold``, background or unmapped classes
    and boxes empty after clamping are dropped. Labels come from ``CLASSES``.
    """
    import numpy as np

    if prediction.ndim != 4 or prediction.shape[-1] != 7:
        raise RuntimeError("Unexpected detector output shape")
    detections = []
    for row in prediction.reshape(-1, 7):
        if not np.isfinite(row).all() or row[2] < threshold:
            continue
        class_id = int(row[1])
        if not 0 < class_id < len(CLASSES):
            continue
        bounds = (np.clip(row[3:7], 0, 1) * np.array([width, height, width, height])).astype(int)
        x1, y1, x2, y2 = map(int, bounds)
        x1, x2 = min(x1, width - 1), min(x2, width - 1)
        y1, y2 = min(y1, height - 1), min(y2, height - 1)
        if x2 <= x1 or y2 <= y1:
            continue
        detections.append(Detection(CLASSES[class_id], round(float(row[2]), 5), (x1, y1, x2, y2)))
    return tuple(detections)


class MobileNetSSD:
    """Pascal VOC MobileNet-SSD through OpenCV DNN on CPU (20 foreground classes).

    Input is stretched to 300 × 300 without letterboxing, matching how SSD models
    are trained; letterboxing this model would change its validated behavior.
    The pinned network's ``DetectionOutput`` drops scores below 0.25 itself.
    """
    name, runtime = "mobilenet-ssd", "opencv-dnn"
    classes = tuple(CLASSES[1:])
    INPUT_SIZE, SCALE, MEAN = 300, 0.007843, (127.5, 127.5, 127.5)

    def __init__(self, name="mobilenet-ssd"):
        self._require_name(name)
        import cv2
        import numpy as np

        verify_model()
        self.cv2, self.np = cv2, np
        cv2.setNumThreads(2)
        self.net = cv2.dnn.readNetFromCaffe(str(ASSETS / MODEL_FILES[0]), str(ASSETS / MODEL_FILES[1]))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    @classmethod
    def _require_name(cls, name):
        if name != cls.name:
            raise ValueError(f"Unknown MobileNet-SSD model: {name}")

    @classmethod
    def model_files(cls, name="mobilenet-ssd"):
        """Checksum-pinned asset names this model loads."""
        cls._require_name(name)
        return MODEL_FILES

    @classmethod
    def preprocessing(cls, name="mobilenet-ssd"):
        """Model input as ``prepare_input`` builds it: ``(pixel - mean) * scale``."""
        cls._require_name(name)
        return {"input_size": [cls.INPUT_SIZE, cls.INPUT_SIZE], "resize": "stretch", "interpolation": "INTER_LINEAR",
                "color_order": "BGR", "layout": "NCHW", "dtype": "float32", "mean": list(cls.MEAN),
                "scale": cls.SCALE, "padding_value": None,
                "box_mapping": "normalized corners times processing dimensions"}

    def detect(self, bgr, threshold):
        validate_input(bgr, threshold)
        height, width = bgr.shape[:2]
        blob, _ = self.prepare_input(bgr)
        self.net.setInput(blob)
        started = time.monotonic()
        prediction = self.net.forward()
        elapsed = (time.monotonic() - started) * 1000
        return DetectionResult(decode_ssd(prediction, width, height, threshold), elapsed)

    def prepare_input(self, bgr):
        """The production model input, also used by the file-only audit."""
        # Python numeric Scalars initialize only the first OpenCV channel. Use
        # all three means explicitly so G/R receive the same model normalization.
        size = (self.INPUT_SIZE, self.INPUT_SIZE)
        return self.cv2.dnn.blobFromImage(bgr, self.SCALE, size, self.MEAN), None


class YoloxDetector:
    """YOLOX ONNX export (COCO, 80 classes) with letterbox input and class-aware NMS.

    Uses ONNX Runtime on CPU when installed, otherwise OpenCV DNN; both run the
    same graph. The 0.1.1rc0 exports take unnormalized BGR 0–255 input, padded
    with 114 at the bottom/right, and return undecoded grid offsets with
    sigmoid objectness and class scores.
    """
    MODELS = {"yolox-nano": ("yolox_nano.onnx", 416)}
    STRIDES = (8, 16, 32)
    PADDING_VALUE = 114

    def __init__(self, name="yolox-nano", runtime="auto", nms_threshold=0.45, threads=2):
        filename, self.size = self._model(name)
        import cv2
        import numpy as np

        (path,) = assets.verify([filename])
        self.cv2, self.np = cv2, np
        self.name, self.classes, self.nms_threshold = name, COCO_CLASSES, nms_threshold
        if runtime == "auto":
            try:
                import onnxruntime  # noqa: F401
                runtime = "onnxruntime"
            except ImportError:
                runtime = "opencv-dnn"
        if runtime == "onnxruntime":
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads, options.inter_op_num_threads = threads, 1
            self.session = ort.InferenceSession(str(path), sess_options=options,
                                                providers=["CPUExecutionProvider"])
            self.input_name = self.session.get_inputs()[0].name
        elif runtime == "opencv-dnn":
            cv2.setNumThreads(threads)
            self.net = cv2.dnn.readNetFromONNX(str(path))
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        else:
            raise ValueError(f"Unknown YOLOX runtime: {runtime}")
        self.runtime = runtime
        grids, strides = [], []
        for stride in self.STRIDES:
            cells = self.size // stride
            ys, xs = np.meshgrid(np.arange(cells), np.arange(cells), indexing="ij")
            grids.append(np.stack((xs, ys), 2).reshape(-1, 2))
            strides.append(np.full((cells * cells, 1), stride))
        self.grid, self.stride = np.concatenate(grids), np.concatenate(strides)

    @classmethod
    def _model(cls, name):
        if name not in cls.MODELS:
            raise ValueError(f"Unknown YOLOX model: {name}")
        return cls.MODELS[name]

    @classmethod
    def model_files(cls, name="yolox-nano"):
        """Checksum-pinned asset names this model loads."""
        return (cls._model(name)[0],)

    @classmethod
    def preprocessing(cls, name="yolox-nano"):
        """Model input as ``prepare_input`` builds it: unnormalized, top-left letterboxed BGR."""
        size = cls._model(name)[1]
        return {"input_size": [size, size], "resize": "letterbox_top_left", "interpolation": "INTER_LINEAR",
                "color_order": "BGR", "layout": "NCHW", "dtype": "float32", "mean": [0, 0, 0], "scale": 1,
                "padding_value": cls.PADDING_VALUE, "box_mapping": "inverse realized x/y resize ratios"}

    def _forward(self, blob):
        if self.runtime == "onnxruntime":
            return self.session.run(None, {self.input_name: blob})[0]
        self.net.setInput(blob)
        return self.net.forward()

    def detect(self, bgr, threshold):
        cv2, np = self.cv2, self.np
        validate_input(bgr, threshold)
        blob, transform = self.prepare_input(bgr)
        started = time.monotonic()
        output = self._forward(blob)
        elapsed = (time.monotonic() - started) * 1000
        expected = (1, len(self.grid), 5 + len(self.classes))
        if output.shape != expected:
            raise RuntimeError(f"Unexpected YOLOX output shape {output.shape}; expected {expected}")
        output = output[0]
        scores = output[:, 4:5] * output[:, 5:]
        class_ids, confidences = scores.argmax(1), scores.max(1)
        keep = np.isfinite(output).all(1) & (confidences >= threshold)
        if not keep.any():
            return DetectionResult((), elapsed)
        centers = (output[keep, :2] + self.grid[keep]) * self.stride[keep]
        sizes = np.exp(np.clip(output[keep, 2:4], -20, 20)) * self.stride[keep]
        corners = np.concatenate((centers - sizes / 2, centers + sizes / 2), 1)
        class_ids, confidences = class_ids[keep], confidences[keep]
        rects = np.concatenate((corners[:, :2], corners[:, 2:] - corners[:, :2]), 1)
        indices = cv2.dnn.NMSBoxesBatched(rects.tolist(), confidences.tolist(), class_ids.tolist(),
                                          threshold, self.nms_threshold)
        detections = []
        for index in sorted(np.array(indices).reshape(-1).tolist(), key=lambda i: -confidences[i]):
            x1, y1, x2, y2 = transform.to_source(*corners[index])
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append(Detection(self.classes[class_ids[index]], round(float(confidences[index]), 5),
                                        (x1, y1, x2, y2)))
        return DetectionResult(tuple(detections), elapsed)

    def prepare_input(self, bgr):
        """The production BGR, top-left letterboxed float32 NCHW input."""
        image, transform = letterbox(bgr, self.size, pad_value=self.PADDING_VALUE)
        return self.np.ascontiguousarray(image.transpose(2, 0, 1)[None], dtype=self.np.float32), transform


DETECTORS = {"mobilenet-ssd": MobileNetSSD, "yolox-nano": YoloxDetector}
DEFAULT_DETECTOR = "mobilenet-ssd"
# The POC score threshold: every command's default, the evaluation's scoring threshold and the
# pin's recorded threshold (docs/reference/detection.md#poc-threshold-and-live-budget).
POC_THRESHOLD = 0.5
# The owner's live budget, accepted 2026-11-07: the inference p95 of a ``mirror_recognize`` run on
# the Frame (``pipeline.inference_ms.p95`` in its report) may not exceed this many milliseconds.
LIVE_INFERENCE_P95_BUDGET_MS = 200


def registered(name):
    """The ``DETECTORS`` class for ``name``; unknown names fail closed."""
    if name not in DETECTORS:
        raise ValueError(f"Unknown detector {name!r}; choose from {', '.join(DETECTORS)}")
    return DETECTORS[name]


def create_detector(name=DEFAULT_DETECTOR, **options):
    return registered(name)(name, **options)


def model_files(name):
    """Checksum-pinned asset names (each in ``scripts/smoke-assets.json``) detector ``name`` loads."""
    return tuple(registered(name).model_files(name))


def preprocessing(name):
    """Structured model-input description of detector ``name``: size, resize, order, normalization."""
    return registered(name).preprocessing(name)


def describe(detector):
    """Report fields identifying the model, runtime and label vocabulary."""
    return {"name": detector.name, "runtime": detector.runtime, "classes": len(detector.classes)}


def annotate(bgr, result):
    """Draw boxes and labels on an owned copy of the image the detector saw."""
    import cv2

    image = bgr.copy()
    for detection in result.detections:
        x1, y1, x2, y2 = detection.box_xyxy
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 230, 0), 2)
        cv2.putText(image, f"{detection.label} {detection.confidence:.0%}", (x1, max(20, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 0), 2)
    return image
