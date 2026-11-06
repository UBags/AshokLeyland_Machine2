"""
Verify Installation After Installing Ultralytics
Run this to make sure GPU support is still working
"""

import sys

print("=" * 70)
print("VERIFICATION: PyTorch + Ultralytics + GPU")
print("=" * 70)

all_good = True

# Test 1: PyTorch
print("\n[1/5] Testing PyTorch...")
try:
    import torch

    print(f"✓ PyTorch installed: {torch.__version__}")

    # Check if it's the GPU version
    if "+cu" in torch.__version__:
        cuda_ver = torch.__version__.split("+")[1]
        print(f"  ✓ GPU-enabled version: {cuda_ver}")
    else:
        print("  ⚠ WARNING: This appears to be CPU-only version!")
        print("    Expected: 2.x.x+cu118 or similar")
        print(f"    Got: {torch.__version__}")
        all_good = False

except ImportError as e:
    print(f"✗ PyTorch not installed: {e}")
    all_good = False

# Test 2: CUDA availability
print("\n[2/5] Testing CUDA availability...")
try:
    import torch

    if torch.cuda.is_available():
        print("✓ CUDA available")
        print(f"  CUDA Version: {torch.version.cuda}")
        print(f"  GPU Count: {torch.cuda.device_count()}")
        print(f"  GPU 0: {torch.cuda.get_device_name(0)}")
    else:
        print("✗ CUDA NOT available")
        print("  PyTorch is installed but cannot access GPU")
        print("  Possible causes:")
        print("    - CPU-only PyTorch installed")
        print("    - NVIDIA drivers not installed")
        print("    - GPU not detected")
        all_good = False
except Exception as e:
    print(f"✗ Error checking CUDA: {e}")
    all_good = False

# Test 3: GPU computation
print("\n[3/5] Testing GPU computation...")
try:
    import torch

    if torch.cuda.is_available():
        x = torch.randn(1000, 1000).cuda()
        y = torch.randn(1000, 1000).cuda()
        z = torch.matmul(x, y)
        print("✓ GPU computation works")
    else:
        print("⚠ Skipped (CUDA not available)")
except Exception as e:
    print(f"✗ GPU computation failed: {e}")
    all_good = False

# Test 4: Ultralytics
print("\n[4/5] Testing Ultralytics...")
try:
    from ultralytics import YOLO
    import ultralytics

    print(f"✓ Ultralytics installed: {ultralytics.__version__}")

    # Try to load a model
    print("  Loading YOLOv8n model...")
    model = YOLO('yolov8n.pt')
    print(f"  ✓ Model loaded on device: {model.device}")

except ImportError as e:
    print(f"✗ Ultralytics not installed: {e}")
    all_good = False
except Exception as e:
    print(f"✗ Error loading YOLO model: {e}")
    all_good = False

# Test 5: YOLO inference on GPU
print("\n[5/5] Testing YOLO inference...")
try:
    from ultralytics import YOLO
    import numpy as np
    import torch

    if torch.cuda.is_available():
        # Create test image
        test_img = np.zeros((640, 640, 3), dtype=np.uint8)

        # Load model and run inference
        model = YOLO('yolov8n.pt')
        results = model(test_img, verbose=False)

        print("✓ YOLO inference completed successfully")
        print(f"  Model device: {model.device}")
        print(f"  Inference ran on: {'GPU' if 'cuda' in str(model.device) else 'CPU'}")
    else:
        print("⚠ Skipped (CUDA not available)")

except Exception as e:
    print(f"✗ YOLO inference failed: {e}")
    import traceback

    traceback.print_exc()
    all_good = False

# Summary
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)

if all_good:
    print("\n✓✓✓ ALL TESTS PASSED ✓✓✓")
    print("\nYour installation is correct:")
    print("  • PyTorch with GPU support: Working")
    print("  • CUDA: Available")
    print("  • Ultralytics: Working")
    print("  • YOLO GPU inference: Working")
    print("\nYou can now use the HandDetector safely!")
else:
    print("\n✗✗✗ SOME TESTS FAILED ✗✗✗")
    print("\nSomething is wrong with your installation.")
    print("\nCommon fixes:")
    print("\n1. If PyTorch is CPU-only:")
    print("   pip uninstall torch torchvision torchaudio -y")
    print("   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118")
    print("\n2. If CUDA drivers missing:")
    print("   Update NVIDIA drivers from nvidia.com")
    print("\n3. If Ultralytics missing:")
    print("   pip install ultralytics --no-deps")
    print("   pip install opencv-python pillow pyyaml requests scipy tqdm pandas seaborn")

print("\n" + "=" * 70)

# Show detailed package versions
print("\nInstalled Package Versions:")
print("-" * 70)

packages = ['torch', 'torchvision', 'ultralytics', 'cv2', 'numpy', 'PIL']
for pkg in packages:
    try:
        if pkg == 'cv2':
            import cv2

            print(f"  opencv-python: {cv2.__version__}")
        elif pkg == 'PIL':
            import PIL

            print(f"  pillow: {PIL.__version__}")
        else:
            mod = __import__(pkg)
            print(f"  {pkg}: {mod.__version__}")
    except ImportError:
        print(f"  {pkg}: NOT INSTALLED")
    except Exception as e:
        print(f"  {pkg}: ERROR - {e}")

print("\n" + "=" * 70)

sys.exit(0 if all_good else 1)