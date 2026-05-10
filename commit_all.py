import os
import subprocess

# Just explicitly add all cartridges instead of .
subprocess.run(['git', 'add', 'cartridges/'])
subprocess.run(['git', 'add', 'README_cartridges.md'])
subprocess.run(['git', 'commit', '-m', 'feat: finalize SAP cartridges implementation and security hardening'])
