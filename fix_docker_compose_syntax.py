import sys

file_path = sys.argv[1]
with open(file_path, "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.startswith("  vault:"):
        pass # It's okay
    new_lines.append(line)

# Let's ensure the docker compose file is syntactically valid by running config
