# Pakistan Prize Bond Checker

Check your prize bond numbers against official draw results from National Savings Pakistan.

## Usage

Open the app: [pakistan-prize-bond-check.streamlit.app](https://pakistan-prize-bond-check.streamlit.app)

Select denomination, pick a draw, enter bond numbers, and check.

## Updating data files

The result files are bundled in `data/`. Run these commands on your local machine (which can reach savings.gov.pk) to refresh:

```bash
source .venv/bin/activate
python scripts/update_data.py
cp -r data/* repo/data/
cd repo
git add data/
git commit -m "Update prize bond data files"
git push
cd ..
```

Data is sourced from [savings.gov.pk](https://savings.gov.pk/download-draws).
