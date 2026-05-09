#!/bin/bash
git rm -rf --cached cartridges/sap_payroll cartridges/sap_time cartridges/sap_checkin cartridges/sap_fi_co cartridges/sap_analytics
rm -rf cartridges/sap_payroll cartridges/sap_time cartridges/sap_checkin cartridges/sap_fi_co cartridges/sap_analytics
python generator.py
find cartridges/sap_payroll cartridges/sap_time cartridges/sap_checkin cartridges/sap_fi_co cartridges/sap_analytics -name "*.pyc" -delete
find cartridges/sap_payroll cartridges/sap_time cartridges/sap_checkin cartridges/sap_fi_co cartridges/sap_analytics -name "__pycache__" -exec rm -rf {} +
git add cartridges/sap_payroll cartridges/sap_time cartridges/sap_checkin cartridges/sap_fi_co cartridges/sap_analytics
