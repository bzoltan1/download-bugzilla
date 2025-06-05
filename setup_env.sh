#!/bin/bash

set -e  # Stop on first error

echo " Updating system and installing required packages..."

sudo zypper refresh
sudo zypper install -y \
    python311 \
    python311-pip \
    ollama

echo "Base packages installed."

# Enable and start Ollama
echo "Enabling and starting Ollama service..."
sudo systemctl enable ollama
sudo systemctl start ollama

# Pull the Mistral model
echo " Pulling Mistral model for Ollama..."
ollama pull mistral

# --- Python environment setup ---
echo "Creating virtual environment: bugzilla-env..."
python3.11 -m venv bugzilla-env
source bugzilla-env/bin/activate

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# --- Optional: run environment test ---
echo "Running environment test..."
python test_env.py
deactivate

echo "Setup complete! Activate with: source bugzilla-env/bin/activate"
