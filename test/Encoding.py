import base64

sample_string = "abcdefgh"
sample_string_bytes = sample_string.encode("utf-8")

base64_bytes = base64.b64encode(sample_string_bytes)
base64_string = base64_bytes.decode("utf-8")

print(f"Encoded string: {base64_string}")

base64_bytes = base64_string.encode("utf-8")

sample_string_bytes1 = base64.b64decode(base64_bytes)
sample_string1 = sample_string_bytes1.decode("utf-8")

print(f"Decoded string: {sample_string1}")