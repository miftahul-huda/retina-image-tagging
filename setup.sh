#!/bin/bash
# setup.sh
# Script instalasi untuk Compute Engine (Debian/Ubuntu)
# Jalankan sekali saat pertama kali setup VM.
#
# Penggunaan:
#   chmod +x setup.sh && ./setup.sh

set -e

echo "=============================================="
echo "  RETINA IMAGE TAGGER - Setup Compute Engine"
echo "=============================================="

# Update package list
echo ""
echo "→ Update package list ..."
sudo apt-get update -y

# Pastikan Python 3 dan pip tersedia
echo ""
echo "→ Install Python 3 dan pip ..."
sudo apt-get install -y python3 python3-pip python3-venv

# Install dependensi sistem untuk Pillow dan font
echo ""
echo "→ Install dependensi sistem (libjpeg, libpng, font DejaVu) ..."
sudo apt-get install -y \
    libjpeg-dev \
    libpng-dev \
    libfreetype6-dev \
    fonts-dejavu-core \
    fonts-dejavu-extra

# Buat virtual environment
echo ""
echo "→ Membuat virtual environment Python ..."
python3 -m venv venv
source venv/bin/activate

# Install dependensi Python
echo ""
echo "→ Install dependensi Python dari requirements.txt ..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "=============================================="
echo "  Setup selesai!"
echo ""
echo "  Cara menjalankan aplikasi:"
echo "  source venv/bin/activate"
echo "  python3 image_tagger.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD"
echo "=============================================="
