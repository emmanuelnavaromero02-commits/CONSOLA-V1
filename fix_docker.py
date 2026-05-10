import os
import glob

for dockerfile in glob.glob('cartridges/*/Dockerfile'):
    with open(dockerfile, 'r') as f:
        content = f.read()

    if 'groupadd' not in content:
        # Inject user creation if it doesn't exist
        content = content.replace('WORKDIR /app', 'RUN groupadd -r appgroup && useradd -r -g appgroup appuser\nWORKDIR /app')
        content = content.replace('COPY . .', 'COPY . .\nRUN chown -R appuser:appgroup /app\nUSER appuser')

        with open(dockerfile, 'w') as f:
            f.write(content)
