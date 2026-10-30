import torch
import ultralytics

print(f"PyTorch: {torch.__version__}")
print(f"Ultralytics: {ultralytics.__version__}")
print(f"CUDA Available: {torch.cuda.is_available()}")
print(f"Has add_safe_globals: {hasattr(torch.serialization, 'add_safe_globals')}")