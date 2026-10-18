"""Download the licensed original and verify its published SHA-256."""
import hashlib
import json
from pathlib import Path
import urllib.request
from ml.data import PUBLIC_SHA


def main():
    folder = Path('data/raw'); folder.mkdir(parents=True, exist_ok=True)
    url = 'https://data.mendeley.com/public-files/datasets/jtsyb593fy/files/3dca37e9-358e-4034-b166-4244082b9177/file_downloaded'
    request = urllib.request.Request(url, headers={'User-Agent': 'SmartGardenResearch/1.0 Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != PUBLIC_SHA:
        raise ValueError('Source checksum changed. Review the dataset before using it.')
    (folder / 'mendeley.xlsx').write_bytes(data)
    import pandas as pd
    pd.read_excel(folder / 'mendeley.xlsx').to_csv(folder / 'mendeley_original.csv', index=False)
    print('Verified dataset saved to data/raw/')


if __name__ == '__main__':
    main()
