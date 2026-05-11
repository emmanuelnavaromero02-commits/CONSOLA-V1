import os
import shutil

CARTRIDGES = [
    "sap_successfactors",
    "sap_hcm",
    "sap_s4hana",
    "sap_payroll",
    "sap_time",
    "sap_checkin",
    "sap_fi_co",
    "sap_analytics"
]

def recreate_cartridges():
    for idx, c in enumerate(CARTRIDGES):
        port = 8202 + idx
        c_dir = f"cartridges/{c}"
        if not os.path.exists(c_dir):
            shutil.copytree("cartridges/replicon", c_dir)

            # Update specific files
            main_path = os.path.join(c_dir, "app/main.py")
            if os.path.exists(main_path):
                with open(main_path, "r") as f:
                    content = f.read()
                content = content.replace("Replicon", c.replace("_", " ").title())
                with open(main_path, "w") as f:
                    f.write(content)

            docker_path = os.path.join(c_dir, "Dockerfile")
            if os.path.exists(docker_path):
                with open(docker_path, "r") as f:
                    content = f.read()
                content = content.replace("8201", str(port))
                with open(docker_path, "w") as f:
                    f.write(content)

recreate_cartridges()
