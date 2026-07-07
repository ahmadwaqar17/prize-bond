"""
Pakistan Prize Bond Checker — Streamlit App
=============================================
An interactive web app (runs locally) that:
  1. Lets you pick a denomination (100, 200, 750, 1500, 25000, 40000)
  2. Fetches the LIVE list of available draw dates directly from the
     official National Savings website (savings.gov.pk/download-draws)
  3. Downloads and parses the actual result file for the draw you pick
     (the site publishes plain .txt files for most draws, and PDFs for
     some older ones — both are supported)
  4. Lets you type your bond number and tells you instantly whether it
     won a prize, and in which prize tier

HOW TO RUN THIS APP
--------------------
1. Install dependencies (one time):
       pip install streamlit requests pdfplumber

2. Run the app:
       streamlit run prize_bond_app.py

3. Your browser will open automatically at http://localhost:8501

NOTE ON THE DATA SOURCE
------------------------
This app fetches files directly from https://savings.gov.pk (the official
government site) at the moment you use it. It does not store or bundle
any result data itself — every check happens live, so results are as
current as what's published on the official website. If the site is slow,
under maintenance, or restructures its pages, this app's fetching logic
may need small updates.
"""

import io
import re

import requests
import streamlit as st

try:
    import pdfplumber
except ImportError:
    pdfplumber = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Each denomination's "Download Draws" listing page on the official site.
DENOMINATION_PAGES = {
    "Rs. 100": "https://savings.gov.pk/rs-100-prize-bond-draw",
    "Rs. 200": "https://savings.gov.pk/rs-200-prize-bond-draw",
    "Rs. 750": "https://savings.gov.pk/rs-750-prize-bond-draw",
    "Rs. 1500": "https://savings.gov.pk/rs-1500-prize-bond-draw",
    "Rs. 25,000 (Premium)": "https://savings.gov.pk/premium-prize-bond-rs-25000",
    "Rs. 40,000 (Premium)": "https://savings.gov.pk/premium-prize-bond-rs-40000",
}

REQUEST_HEADERS = {
    # A normal browser-like User-Agent makes the request less likely to be
    # blocked by basic bot filters.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}



# A real prize-tier header line always contains the word "prize" AND a
# comma-formatted rupee amount (e.g. "1,500,000" or "9,300"). This lets us
# tell an actual prize header apart from decorative title lines like
# "DRAW OF Rs.750/- PRIZE BOND", which contain "prize" but no comma amount.
HEADER_PATTERN = re.compile(r"prize.*,\d{3}", re.IGNORECASE)

# A standalone divider line made only of dashes/underscores/whitespace.
DIVIDER_PATTERN = re.compile(r"[-_=\s]+")

# Bond numbers in these files are typically 3-7 digit tokens.
NUMBER_PATTERN = re.compile(r"\d{3,7}")

# Link to a result file: href ending in .txt/.pdf/.doc/.docx
LINK_PATTERN = re.compile(
    r'<a[^>]+href="([^"]+\.(?:txt|pdf|doc|docx))"[^>]*>([^<]*)</a>',
    re.IGNORECASE,
)

# Try to pull a draw number out of a filename/label, e.g. "...-105.txt",
# "105th-draw-of...", "Draw-Result-105th-draw..."
# (requires the word "draw" nearby so we don't grab the denomination instead)
DRAW_NUMBER_PATTERN = re.compile(r"(\d{1,3})(?:st|nd|rd|th)?[-_]?draw", re.IGNORECASE)
DRAW_NUMBER_FALLBACK_PATTERN = re.compile(r"draw[-_]?(\d{1,3})", re.IGNORECASE)

# Known denomination values — if a "draw number" candidate matches one of
# these exactly, it's almost certainly the denomination leaking into the
# match (e.g. "...-Rs-750.txt"), not a real draw number, so we discard it.
KNOWN_DENOMINATIONS = {"100", "200", "750", "1500", "7500", "15000", "25000", "40000"}

# Date embedded in a label like "15-04-2026" or "15/04/2026"
DATE_PATTERN = re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})")


def prettify_draw_label(label: str, url: str) -> str:
    """
    Build a nicer 'Draw #106 - 15/04/2026' style label when we can confidently
    detect a real draw number and date, falling back to the raw label
    otherwise. Draw numbers are only trusted when found next to the literal
    word "draw" (in "105th-draw" or "draw-105" style), which avoids
    mistaking the denomination (e.g. "750") embedded in the filename for
    the draw number.
    """
    combined = f"{label} {url}"

    draw_num = None
    for pattern in (DRAW_NUMBER_PATTERN, DRAW_NUMBER_FALLBACK_PATTERN):
        m = pattern.search(combined)
        if m and m.group(1) not in KNOWN_DENOMINATIONS:
            draw_num = m.group(1)
            break

    date_str = None
    m = DATE_PATTERN.search(label)
    if m:
        date_str = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"

    if draw_num and date_str:
        return f"Draw #{draw_num}  -  {date_str}"
    if date_str:
        return date_str
    return label


# ---------------------------------------------------------------------------
# Data fetching (cached so repeat lookups are instant)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def get_draw_links(listing_page_url: str):
    """
    Scrape a denomination's 'Download Draws' listing page for every link to
    a result file (.txt/.pdf/.doc), returning a list of (label, url) tuples,
    most recent first (the site already lists them newest-year-first).
    """
    resp = requests.get(listing_page_url, headers=REQUEST_HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text

    links = []
    seen_urls = set()
    for match in LINK_PATTERN.finditer(html):
        url, label = match.group(1), match.group(2).strip()
        if url in seen_urls:
            continue
        seen_urls.add(url)
        if not label:
            label = url.rsplit("/", 1)[-1]
        links.append((label, url))
    return links


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_and_parse_draw(file_url: str):
    """
    Download a single draw's result file and parse it into a list of
    (prize_label, number_as_int, number_as_string) tuples.
    """
    resp = requests.get(file_url, headers=REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()

    if file_url.lower().endswith(".pdf"):
        if pdfplumber is None:
            raise RuntimeError("pdfplumber is not installed (pip install pdfplumber)")
        text_parts = []
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
        text = "\n".join(text_parts)
    else:
        # .txt (and best-effort for .doc/.docx, which are plain enough
        # in practice on this site to decode as text)
        text = resp.content.decode("utf-8", errors="ignore")

    return parse_draw_text(text)


def parse_draw_text(text: str):
    """
    Turn the raw result-file text into a list of
    (prize_label, number_int, number_str) tuples.
    """
    results = []
    current_label = None
    started = False  # only start collecting numbers once we hit a real prize header

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if HEADER_PATTERN.search(stripped):
            current_label = stripped
            started = True
            continue

        if not started:
            # Still in the metadata block (draw no., series, date, title...)
            continue

        if DIVIDER_PATTERN.fullmatch(stripped):
            continue

        for n in NUMBER_PATTERN.findall(stripped):
            results.append((current_label, int(n), n))

    return results


def check_number(parsed_numbers, bond_number_str: str):
    """Return all (label, raw_number) matches for the given bond number."""
    try:
        target = int(bond_number_str.strip())
    except (ValueError, AttributeError):
        return None  # invalid input
    return [(label, raw) for label, num, raw in parsed_numbers if num == target]


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Pakistan Prize Bond Checker", page_icon="🎟️", layout="centered")

st.title("🎟️ Pakistan Prize Bond Checker")
st.caption(
    "Live results fetched directly from the official National Savings "
    "website (savings.gov.pk). Nothing is stored — every check happens "
    "in real time. (Not affiliated with any third-party mirror site.)"
)

# --- Step 1: Denomination ---
denom_label = st.selectbox("1️⃣ Select your bond denomination", list(DENOMINATION_PAGES.keys()))
listing_url = DENOMINATION_PAGES[denom_label]

# --- Step 2: Draw date ---
with st.spinner("Fetching available draw dates from savings.gov.pk..."):
    try:
        draw_links = get_draw_links(listing_url)
    except Exception as e:
        draw_links = []
        st.error(
            f"Couldn't reach the official listing page ({e}). "
            "The site may be temporarily down, or its page structure may "
            "have changed. You can also paste a direct file URL below instead."
        )

CHECK_ALL_SENTINEL = -1
chosen_url = None
chosen_draw_label = None
check_all_draws = False

if draw_links:
    pretty_labels = [prettify_draw_label(label, url) for label, url in draw_links]
    options = [CHECK_ALL_SENTINEL] + list(range(len(pretty_labels)))

    def _format_option(i):
        if i == CHECK_ALL_SENTINEL:
            return f"✅ Check ALL available draws ({len(pretty_labels)} draws)"
        return pretty_labels[i]

    choice_idx = st.selectbox(
        "2️⃣ Select the draw",
        options=options,
        format_func=_format_option,
    )

    if choice_idx == CHECK_ALL_SENTINEL:
        check_all_draws = True
    else:
        chosen_url = draw_links[choice_idx][1]
        chosen_draw_label = draw_links[choice_idx][0]
        st.caption(f"Source file: {chosen_url}")

st.markdown("**...or paste a direct result file URL instead:**")
manual_url = st.text_input("Direct .txt or .pdf URL (optional — overrides the dropdown above)")
if manual_url.strip():
    chosen_url = manual_url.strip()
    check_all_draws = False

uploaded_file = st.file_uploader("**...or upload a result file (.txt or .pdf):**", type=["txt", "pdf"])

# --- Step 3: Bond number(s) ---
st.markdown("**3️⃣ Enter your prize bond number(s)**")
numbers_raw = st.text_area(
    "One number, or several separated by commas or new lines",
    placeholder="e.g.\n900286\n134617, 197462",
    height=100,
)

check_clicked = st.button("🔍 Check My Bond(s)", type="primary", use_container_width=True)

if check_clicked:
    bond_numbers = [n.strip() for n in re.split(r"[,\n]+", numbers_raw) if n.strip()]

    if not bond_numbers:
        st.warning("Please enter at least one bond number.")
    else:
        parsed_by_draw = {}
        draws_to_check = []

        if uploaded_file:
            # Parse the uploaded file directly
            try:
                raw = uploaded_file.read()
                if uploaded_file.name.lower().endswith(".pdf"):
                    if pdfplumber is None:
                        raise RuntimeError("pdfplumber is not installed")
                    text_parts = []
                    with pdfplumber.open(io.BytesIO(raw)) as pdf:
                        for page in pdf.pages:
                            text_parts.append(page.extract_text() or "")
                    text = "\n".join(text_parts)
                else:
                    text = raw.decode("utf-8", errors="ignore")
                parsed = parse_draw_text(text)
                label = uploaded_file.name
                parsed_by_draw[label] = parsed
                draws_to_check = [(label, label, None)]
            except Exception as e:
                st.error(f"Couldn't parse uploaded file: {e}")

        elif check_all_draws:
            draws_to_check = [
                (label, prettify_draw_label(label, url), url) for label, url in draw_links
            ]
        elif chosen_url:
            raw_label = chosen_draw_label or "Selected draw"
            pretty_label = prettify_draw_label(chosen_draw_label, chosen_url) if chosen_draw_label else "Selected draw"
            draws_to_check = [(raw_label, pretty_label, chosen_url)]
        else:
            st.warning("Please select a draw, paste a file URL, or upload a file.")

        if draws_to_check and not uploaded_file:
            progress = st.progress(0.0, text="Downloading official result files...")
            for i, (raw_label, _, url) in enumerate(draws_to_check):
                try:
                    parsed_by_draw[raw_label] = fetch_and_parse_draw(url)
                except Exception as e:
                    parsed_by_draw[raw_label] = None
                    st.warning(f"Couldn't fetch/parse **{raw_label}**: {e}")
                progress.progress((i + 1) / len(draws_to_check))
            progress.empty()

        if parsed_by_draw:
            st.divider()

            for bond_number in bond_numbers:
                st.subheader(f"Bond Number: {bond_number}")

                try:
                    int(bond_number)
                except ValueError:
                    st.error("Not a valid numeric bond number — skipped.")
                    continue

                any_win = False
                any_valid_draw = False
                for raw_label, pretty_label, _ in draws_to_check:
                    parsed = parsed_by_draw.get(raw_label)
                    if parsed is None:
                        continue
                    any_valid_draw = True
                    matches = check_number(parsed, bond_number)
                    if matches:
                        any_win = True
                        display = pretty_label if "  -  " in pretty_label else raw_label
                        st.success(f"🎉 WON in **{display}**")
                        for label, raw in matches:
                            st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;— **{label}**")

                if not any_valid_draw:
                    st.warning("No draw data could be checked for this number.")
                elif not any_win:
                    st.info(
                        f"❌ Not found in this draw. "
                        "Your principal is always safe regardless — you can "
                        "encash the bond at face value anytime."
                    )

            st.caption(
                "Always double-check against the official result on savings.gov.pk "
                "before making any prize claim, and make sure the denomination and "
                "draw date match your physical bond exactly."
            )

with st.expander("ℹ️ About this tool / troubleshooting"):
    st.markdown(
        """
- This tool reads files that National Savings Pakistan publishes publicly
  at **savings.gov.pk/download-draws**. It does not use any private or
  unofficial data source.
- Older draws are sometimes only available as `.doc` files instead of
  `.txt`/`.pdf` — those may not parse cleanly. Use the manual URL box to try
  a different file if a draw doesn't work.
- If the dropdown fails to load, the government site may be rate-limiting
  or temporarily blocking automated requests. Try again shortly, or fetch
  the file yourself and paste its direct URL into the manual box.
- This is an unofficial, independent tool. Always confirm a winning result
  on the official site before taking any action.
        """
    )