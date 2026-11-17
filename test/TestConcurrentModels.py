from camera.BungSegmenter import BungSegmenter
from camera.HexagonNutDetector import HexagonNutDetector
from camera.HubAndBearingSegmenter import HubAndBearingSegmenter
from camera.ModelManager import ModelManager

# Verify same instance
manager1 = ModelManager.get_instance()
manager2 = ModelManager.get_instance()
assert manager1 is manager2

# Verify models shared
detector = HexagonNutDetector()
detector.load_models()
segmenter = BungSegmenter(None, None)
assert detector.sam_predictor is segmenter.sam_predictor
assert detector.yolo_model is segmenter.yolo_model

import torch

# Before: measure GPU memory with old implementation
# After: measure GPU memory with ModelManager
if torch.cuda.is_available():
    print(f"GPU Memory Allocated: {torch.cuda.memory_allocated() / 1e9:.2f} GB")
    print(f"GPU Memory Reserved: {torch.cuda.memory_reserved() / 1e9:.2f} GB")

# Test that multiple detectors can run concurrently
bung = BungSegmenter(None, None)
nut = HexagonNutDetector()
nut.load_models()
hub = HubAndBearingSegmenter(None, None)

# All should work without conflicts


# Method 1: BungSegmenter / HubAndBearingSegmenter style (auto-load)
# segmenter = BungSegmenter(None, None)  # Models auto-loaded
# count, components, gamma = segmenter.segment_holes_batch(...)
#
# # Method 2: HexagonNutDetector style (explicit load)
# detector = HexagonNutDetector()
# detector.load_models()
# is_detected, center, contour = detector.processImage(image_rgb)

# All methods access the same singleton ModelManager

# mobile_sam_path = "<project_root>/models/MobileSAM/weights/mobile_sam.pt"
# yolo_path = "<project_root>/models/MobileSAM/MobileSAMv2/weight/ObjectAwareModel.pt"