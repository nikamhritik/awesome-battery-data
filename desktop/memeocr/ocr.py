"""Chinese OCR with models bundled in the pinned RapidOCR wheel."""

from .images import read_image


class Recognizer:
    def __init__(self):
        import onnxruntime

        onnxruntime.disable_telemetry_events()
        from rapidocr_onnxruntime import RapidOCR

        self.engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)

    def __call__(self, item) -> str:
        import numpy as np

        with read_image(item.path) as image:
            # RapidOCR interprets an ndarray as OpenCV BGR; discard alpha on white.
            from PIL import Image

            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            pixels = np.asarray(background)[:, :, ::-1].copy()
            background.close()
            result, _ = self.engine(pixels)
        return "\n".join(line[1] for line in (result or []))
