import easyocr
import os

# path to your image in Downloads
image_path = os.path.join(os.path.expanduser('~'), 'Downloads', 'magic.jpg')

# english selected
reader = easyocr.Reader(['en'])
results = reader.readtext(image_path)

# output
print("\n--- Extracted Text ---\n")
for bbox, text, conf in results:
    print(f"{text}")

print("\n--- Done ---")
#test