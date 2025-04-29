#!/bin/bash

sudo apt update -y
sudo apt install python3-venv -y
sudo python3 -m venv /opt/k8s-pod-schedule-check/venv
sudo cp * /opt/k8s-pod-schedule-check/
sudo /opt/k8s-pod-schedule-check/venv/bin/pip install -r requirements.txt
alias pod-schedule-check='/opt/k8s-pod-schedule-check/venv/bin/python /opt/k8s-pod-schedule-check/main.py'
echo "alias pod-schedule-check='/opt/k8s-pod-schedule-check/venv/bin/python /opt/k8s-pod-schedule-check/main.py'" >> ~/.bashrc
