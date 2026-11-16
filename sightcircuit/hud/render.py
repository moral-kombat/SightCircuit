"""Fixed HUD tile drawing: names and approximate view-direction arrows only.

A row without a direction (``contract.NO_DIRECTION``) shows its name alone.
"""


def draw_tile(rows, config):
    """Draw selected rows on a blank tile.

    Deliberately takes no scene image: no video, boxes or leader lines can
    reach the HUD through this function.
    """
    import cv2
    import numpy as np
    image = np.zeros((config.height_px, config.width_px, 3), dtype=np.uint8)
    cv2.putText(image, "View direction (approx.)", (10, 18), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (180, 180, 180), 1, cv2.LINE_AA)
    for index, row in enumerate(rows):
        y = 43 + 29 * index
        dx, dy = row.direction or (None, None)
        if dx is None:  # A names-only row (contract.NO_DIRECTION): no arrow and no dot.
            pass
        elif dx or dy:
            cv2.arrowedLine(image, (23 - 8 * dx, y - 8 * dy), (23 + 8 * dx, y + 8 * dy),
                            (70, 230, 130), 2, cv2.LINE_AA, tipLength=0.4)
        else:
            cv2.circle(image, (23, y), 4, (70, 230, 130), 2, cv2.LINE_AA)
        cv2.putText(image, row.label, (47, y + 6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (235, 235, 235), 1, cv2.LINE_AA)
    image.flags.writeable = False
    return image
