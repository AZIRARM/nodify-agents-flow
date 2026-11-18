#!/bin/bash
set -e

# 1. Démarrer le serveur Ollama en arrière-plan
echo "Starting Ollama server in the background..."
/bin/ollama serve &
pid=$!

# 2. Attendre que le serveur soit prêt à répondre (avec un timeout de 60s)
echo "Waiting for Ollama to be ready..."
timeout=60
while ! ollama list >/dev/null 2>&1; do
  sleep 1
  timeout=$((timeout - 1))
  if [ $timeout -le 0 ]; then
    echo "Error: Ollama server did not start in time."
    exit 1
  fi
done
echo "Ollama server is ready."

# 3. Télécharger le modèle s'il n'est pas déjà présent
# La variable OLLAMA_MODEL est définie dans docker-compose.yml
if [ -n "$OLLAMA_MODEL" ]; then
  echo "Checking for model: $OLLAMA_MODEL"
  # 'ollama show' échoue si le modèle n'existe pas, on peut donc l'utiliser comme test
  if ! ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
    echo "Model $OLLAMA_MODEL not found. Pulling..."
    ollama pull "$OLLAMA_MODEL"
    echo "Model $OLLAMA_MODEL pulled successfully."
  else
    echo "Model $OLLAMA_MODEL already present, skipping pull."
  fi
else
  echo "OLLAMA_MODEL is not set, skipping model pull."
fi

# 4. Attendre la fin du processus serveur pour que le conteneur reste actif
echo "Ollama is ready to serve requests."
wait $pid