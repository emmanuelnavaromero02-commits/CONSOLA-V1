import subprocess
import os

subprocess.run(["git", "branch", "-D", "feat-native-sap-cartridges"], stderr=subprocess.DEVNULL)
subprocess.run(["git", "checkout", "-b", "feat-native-sap-cartridges"])
