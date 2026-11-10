import urllib.request, urllib.error
UA = {"User-Agent": "local-legal-data/1.0 (+https://github.com/MileMarkMedia/Local-legal-data)"}
tests = [
 ("api", "https://crashviewer.nhtsa.dot.gov/CrashAPI/crashes/GetCrashesByLocation?fromCaseYear=2021&toCaseYear=2021&state=6&county=73&format=json", {}),
 ("zip-2022", "https://static.nhtsa.gov/nhtsa/downloads/FARS/2022/National/FARS2022NationalCSV.zip", {"Range": "bytes=0-99"}),
 ("zip-2023", "https://static.nhtsa.gov/nhtsa/downloads/FARS/2023/National/FARS2023NationalCSV.zip", {"Range": "bytes=0-99"}),
 ("zip-2024", "https://static.nhtsa.gov/nhtsa/downloads/FARS/2024/National/FARS2024NationalCSV.zip", {"Range": "bytes=0-99"}),
]
for name, url, extra in tests:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **extra}), timeout=40) as r:
            body = r.read(400)
            print(name, r.status, r.headers.get("Content-Type"), r.headers.get("Content-Range") or r.headers.get("Content-Length"), repr(body[:300]), flush=True)
    except urllib.error.HTTPError as e:
        print(name, "HTTP", e.code, repr(e.read(300)), flush=True)
    except Exception as e:
        print(name, "ERR", type(e).__name__, e, flush=True)
