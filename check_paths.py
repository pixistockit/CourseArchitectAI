import os

# The exact path you confirmed exists
target_path = "/home/ubuntu/course-architect-ai/audit_slide/input_files"

print(f"--- PATH CHECK ---")
print(f"Looking for: {target_path}")

if os.path.exists(target_path):
    print("✅ SUCCESS: Path exists!")
    print(f"Contents: {os.listdir(target_path)}")
else:
    print("❌ FAILURE: Path not found.")