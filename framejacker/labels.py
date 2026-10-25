"""Label vocabularies and the canonical names used to compare detectors.

Canonical names are COCO's 80 categories. Pascal VOC names map onto them
(``sofa`` → ``couch``). Neither vocabulary contains rug or carpet.
"""
COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush")
VOC_TO_CANONICAL = {"aeroplane": "airplane", "motorbike": "motorcycle", "sofa": "couch",
                    "tvmonitor": "tv", "diningtable": "dining table", "pottedplant": "potted plant"}


def canonical(label):
    return VOC_TO_CANONICAL.get(label, label)
