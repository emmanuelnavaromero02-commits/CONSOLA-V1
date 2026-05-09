import os
import shutil

CARTRIDGES = [
    {"id": "sap_payroll", "name": "SAP Payroll", "port": "8204", "class": "SapPayrollClient", "env_prefix": "sap_payroll"},
    {"id": "sap_time", "name": "SAP Time Management", "port": "8205", "class": "SapTimeClient", "env_prefix": "sap_time"},
    {"id": "sap_checkin", "name": "SAP Check-In Empleados", "port": "8206", "class": "SapCheckinClient", "env_prefix": "sap_checkin"},
    {"id": "sap_fi_co", "name": "SAP FI/CO Finanzas", "port": "8207", "class": "SapFiCoClient", "env_prefix": "sap_fico"},
    {"id": "sap_analytics", "name": "SAP Analytics", "port": "8208", "class": "SapAnalyticsClient", "env_prefix": "sap_analytics"}
]

SRC = "cartridges/sap_hcm"

def replace_in_file(filepath, replacements):
    with open(filepath, "r") as f:
        content = f.read()
    for old, new in replacements.items():
        content = content.replace(old, new)
    with open(filepath, "w") as f:
        f.write(content)

for c in CARTRIDGES:
    cid = c["id"]
    dst = f"cartridges/{cid}"
    if os.path.exists(dst):
        shutil.rmtree(dst)

    # Copy source avoiding __pycache__ and .pyc
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))

    # Rename dags files
    os.rename(f"{dst}/dags/sap_hcm_extract.py", f"{dst}/dags/{cid}_extract.py")
    os.rename(f"{dst}/dags/sap_hcm_extract_all.py", f"{dst}/dags/{cid}_extract_all.py")

    # Common replacements
    repls = {
        "sap_hcm": cid,
        "SAP HCM Core": c["name"],
        "SAP HCM Cartridge": f"{c['name']} Cartridge",
        "8202": c["port"],
        "SapHcmClient": c["class"],
        "SAP_HCM": c["env_prefix"].upper(),
        "sap_hcm_base_url": f"{c['env_prefix']}_base_url",
        "sap_hcm_user": f"{c['env_prefix']}_user",
        "sap_hcm_pass": f"{c['env_prefix']}_pass",
        "sap_hcm_client": f"{c['env_prefix']}_client",
        "get_sap_hcm_credentials": f"get_{cid}_credentials",
        "SAP HCM": c["name"], # generic string replacement
        "Sap_payrollClient": "SapPayrollClient",
        "Sap_timeClient": "SapTimeClient",
        "Sap_checkinClient": "SapCheckinClient",
        "Sap_fi_coClient": "SapFiCoClient",
        "Sap_analyticsClient": "SapAnalyticsClient"
    }

    for root, dirs, files in os.walk(dst):
        for file in files:
            filepath = os.path.join(root, file)
            # Skip yaml and sql since we will overwrite them
            if file in ["connector.yaml", "entities.yaml", "knowledge_bits.yaml", "seed.sql"]:
                continue
            replace_in_file(filepath, repls)

print("Generated copies.")
