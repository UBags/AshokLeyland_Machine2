import numpy as np
from scipy.ndimage import minimum_filter, maximum_filter
import matplotlib.pyplot as plt


def rgb2gray(rgb: np.ndarray) -> np.ndarray:
    """Standard RGB → grayscale conversion."""
    return np.dot(rgb[..., :3], [0.299, 0.587, 0.114])


def contrast_norm_equivalent(
        image: np.ndarray,
        size: int = 51,
        min_diff: int = 50
) -> np.ndarray:
    """
    Updated version: handles color inputs (converts to gray),
    works with float (0-1) or uint8 images from plt.imread.
    """
    # Convert to grayscale if color
    if image.ndim == 3:
        gray = rgb2gray(image)
    else:
        gray = image.copy()

    # Convert to float32 for calculations (handles uint8 or float 0-1)
    if gray.dtype != np.float32:
        if gray.max() <= 1.0:
            gray = gray * 255.0
        gray = gray.astype(np.float32)

    # Ensure odd size for centering
    if size % 2 == 0:
        size += 1

    # Local min/max with overlapping windows
    local_min = minimum_filter(gray, size=size)
    local_max = maximum_filter(gray, size=size)

    # Force minimum range in low-contrast areas
    diff = local_max - local_min
    denom = np.maximum(diff, min_diff)

    # Stretch: map local_min → 0, local_max → 255
    normalized = (gray - local_min) / denom * 255.0
    normalized = np.clip(normalized, 0, 255)

    return normalized.astype(np.uint8)


# === Load your image here ===
filename = 'C:/AshokLeylandImages/DOSTPLUS/LHS/2 - Hub and Bottom Bearing - 1.png'  # Change to your saved filename (e.g., input.png)
img = plt.imread(filename)

# Apply normalization — experiment with these params!
size = 101  # Try 31–201; larger = smoother, more propagation into uniform areas
min_diff = 30  # Try 10–60; lower = more amplification in flat regions

output = contrast_norm_equivalent(img, size=size, min_diff=min_diff)

# === Display side-by-side ===
fig, axs = plt.subplots(1, 2, figsize=(14, 7))

# Left: original (keep color if input is color)
axs[0].imshow(img)
axs[0].set_title('Input (Original)')
axs[0].axis('off')

# Right: normalized (grayscale output)
axs[1].imshow(output, cmap='gray')
axs[1].set_title(f'Output (size={size}, min_diff={min_diff})')
axs[1].axis('off')

plt.tight_layout()
plt.show()