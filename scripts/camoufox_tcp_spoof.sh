#!/bin/bash
# camoufox_tcp_spoof.sh
# Applies OS-level TCP spoofing for Linux hosts to achieve Layer 4 Parity with Windows.
# Run this script with root privileges before starting Camoufox on a Linux host.

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)"
  exit 1
fi

echo "Applying Windows OS-Level TCP Spoofing on Linux..."

# 1. IP TTL auf 128 (Windows-Standard) anpassen fuer alle ausgehenden Pakete
# Dies verhindert, dass passive OS-Fingerprinting-Tools (p0f) den Host als Linux (TTL 64) erkennen.
iptables -t mangle -A POSTROUTING -p tcp -j HL --hlim-set 128

# 2. MSS (Maximum Segment Size) anpassen, um Windows-artige Window-Sizes zu triggern
# MTU 1500 - 40 bytes header = 1460 (Typical Windows MSS)
iptables -t mangle -A POSTROUTING -p tcp -m tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1460

# 3. TCP Window Size und Scaling (sysctl) fuer Paritaet
# Temporaere Anpassung der Linux-Kernel-Parameter
sysctl -w net.ipv4.tcp_window_scaling=1
sysctl -w net.ipv4.tcp_rmem="8192 87380 6291456" # Chrome/Windows typical receive windows
sysctl -w net.ipv4.tcp_wmem="8192 65536 4194304"

echo "TCP Spoofing applied successfully. To flush iptables later, use: iptables -t mangle -F POSTROUTING"
