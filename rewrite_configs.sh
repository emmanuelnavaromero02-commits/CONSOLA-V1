for c in cartridges/sap_*; do
    name=$(basename "$c")
    sed -i "s/replicon/$name/g" "$c/app/config/connector.yaml"
    sed -i "s/replicon/$name/g" "$c/app/config/entities.yaml"
    sed -i "s/replicon/$name/g" "$c/app/config/knowledge_bits.yaml"
    sed -i "s/replicon/$name/g" "$c/app/core/config.py"
    sed -i "s/replicon/$name/g" "$c/config/seed.sql"
    mv "$c/app/core/replicon_client.py" "$c/app/core/sap_client.py"
    sed -i "s/RepliconClient/DummyClient/g" "$c/app/services/extraction_service.py"
    sed -i "s/replicon_client/sap_client/g" "$c/app/services/extraction_service.py"
done
