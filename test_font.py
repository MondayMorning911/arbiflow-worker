import urllib.request
try:
    urllib.request.urlopen("https://github.com/google/fonts/raw/main/ofl/roboto/Roboto-Regular.ttf")
    print("OFL works")
except Exception as e:
    print("OFL failed:", e)
