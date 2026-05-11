import subprocess
subprocess.run(["git", "add", "cartridges/"])
subprocess.run(["git", "commit", "-m", "chore: regenerate missing functional base for SAP cartridges extending from Replicon core components"])
