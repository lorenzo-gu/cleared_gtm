# cleared_gtm

## Colorado restaurant trade names

`scripts/colorado_tradenames.py` pulls new trade names from the Colorado Trade Names dataset on data.colorado.gov, keeps the ones that look like food or drink businesses (keywords matched in `tradenameDescription` or `registrantOrganization`), and appends them to `data/colorado_restaurant_tradenames.csv`.

- First run (no CSV yet): last 183 days by `effectiveDate`.
- Every later run: last 7 days, skipping any `masterTradenameId` already in the CSV.
- `.github/workflows/colorado-tradenames.yml` runs it daily at 13:17 UTC and commits the updated CSV. Run it manually from the Actions tab ("Run workflow").

Optional repository secret: `SOCRATA_APP_TOKEN` raises API rate limits.

Edit `KEYWORDS` in the script to widen or narrow the filter. Set `DATASET_ID` if the dataset id differs from `u7sb-g482`.
