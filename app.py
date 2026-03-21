#!/usr/bin/env python3
"""
AI News Dashboard
Fetches the last 3 days of AI news from multiple sources.

Setup:
    pip install flask requests beautifulsoup4 lxml trafilatura

Run:
    python app.py

Then open: http://localhost:5000
"""

import os
from flask import Flask, jsonify, render_template_string, request
import requests as req_lib
from bs4 import BeautifulSoup
from datetime import date, datetime, timedelta
import re
from collections import Counter
import string
import json
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

CACHE = {
    'data': None,
    'timestamp': None,
    'ttl_seconds': 600,
}

STOP_WORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'this', 'that', 'these',
    'those', 'it', 'its', 'they', 'them', 'their', 'we', 'our', 'you',
    'your', 'he', 'she', 'his', 'her', 'as', 'not', 'no', 'also', 'just',
    'more', 'out', 'up', 'if', 'into', 'which', 'how', 'when', 'what',
    'who', 'all', 'one', 'two', 'three', 'i', 'us', 'am', 'than', 'then',
    'so', 'after', 'before', 'over', 'under', 'while', 'since', 'through',
    'between', 'during', 'without', 'within', 'down', 'off', 'above', 'near',
    'any', 'each', 'both', 'other', 'some', 'said', 'about', 'new', 'use',
    'using', 'used', 'get', 'make', 'like', 'time', 'year', 'day', 'way',
    'first', 'next', 'even', 'now', 'back', 'well', 'only', 'much', 'very',
    'still', 'here', 'most', 'help', 'work', 'set', 'such', 'take',
    'where', 'need', 'say', 'know', 'look', 'give', 'include', 'including',
    'across', 'around', 'come', 'came', 'per', 'been', 'those', 'there',
    'their', 'these', 'than', 'that', 'with', 'from', 'have', 'will',
}

# Known AI companies for company detection in "Various" sources
KNOWN_COMPANIES = sorted([
    'Google DeepMind', 'Google', 'OpenAI', 'Microsoft', 'Meta', 'Apple',
    'Amazon', 'NVIDIA', 'Anthropic', 'DeepMind', 'xAI', 'Mistral AI',
    'Mistral', 'Hugging Face', 'Cohere', 'Stability AI', 'Midjourney',
    'Runway', 'ElevenLabs', 'Perplexity', 'Replit', 'GitHub', 'Tesla',
    'Samsung', 'IBM', 'Oracle', 'Salesforce', 'Adobe', 'Palantir',
    'Scale AI', 'Databricks', 'Snowflake', 'DeepSeek', 'Baidu', 'Alibaba',
    'Tencent', 'ByteDance', 'Inflection', 'Character.AI', 'Character AI',
    'LangChain', 'Pinecone', 'Weaviate', 'AWS', 'Azure', 'TSMC', 'Intel',
    'AMD', 'Qualcomm', 'Arm', 'Cursor', 'Windsurf', 'Bolt', 'Lovable',
    'Vercel', 'Notion', 'Suno', 'Descript', 'n8n', 'Jasper', 'Asana',
    'Atlassian', 'Datadog', 'Elastic', 'Pipedream', 'Bardeen', 'Gumroad',
    'Flowith', 'Gamma', 'Assisterr', 'Higgsfield', 'Wispr', 'Moonshot',
    'Qwen', 'Artlist', 'Capacity', 'Haystack', 'deepset', 'Skew',
    'ChatGPT', 'Gemini', 'Llama', 'Grok', 'Claude',
    'Canva', 'Figma', 'Zapier', 'Helicone', 'Atlas AI', 'Pinecone',
    'Runway', 'Replit', 'Bloomberg', 'Reuters', 'TechCrunch', 'Verge',
], key=len, reverse=True)  # longest match first

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
}

SOURCES = [
    # ── AI Labs / Frontier Models ─────────────────────────────────────────
    {"url": "https://openai.com/pt-BR/news/",                                    "company": "OpenAI",            "website": "OpenAI News"},
    {"url": "https://www.anthropic.com/news",                                    "company": "Anthropic",         "website": "Anthropic News"},
    {"url": "https://mistral.ai/news",                                           "company": "Mistral AI",        "website": "Mistral AI News"},
    {"url": "https://x.ai/news",                                                 "company": "xAI",               "website": "xAI News"},
    {"url": "https://www.moonshot.ai/",                                          "company": "Moonshot AI",       "website": "Moonshot AI"},
    {"url": "https://qwen.ai/news",                                              "company": "Alibaba / Qwen",    "website": "Qwen News"},
    # ── Big Tech ──────────────────────────────────────────────────────────
    {"url": "https://blog.google/innovation-and-ai/",                            "company": "Google",            "website": "Google AI Blog",             "rss_url": "https://blog.google/rss/"},
    {"url": "https://antigravity.google/blog",                                   "company": "Google",            "website": "Google Antigravity Blog"},
    {"url": "https://microsoft.ai/news/",                                        "company": "Microsoft",         "website": "Microsoft AI News"},
    {"url": "https://www.microsoft.com/en-us/microsoft-copilot/blog/tag/news/",  "company": "Microsoft",         "website": "Microsoft Copilot Blog"},
    {"url": "https://www.microsoft.com/en-us/research/project/autogen/news-and-awards/", "company": "Microsoft Research", "website": "AutoGen News"},
    {"url": "https://ai.meta.com/blog/",                                         "company": "Meta",              "website": "AI at Meta Blog"},
    {"url": "https://www.alibabagroup.com/en-US/news-and-resource",              "company": "Alibaba Group",     "website": "Alibaba News"},
    {"url": "https://nvidianews.nvidia.com/",                                    "company": "NVIDIA",            "website": "NVIDIA Newsroom"},
    {"url": "https://ir.tesla.com/press",                                        "company": "Tesla",             "website": "Tesla Press"},
    # ── AI Tools / Assistants ─────────────────────────────────────────────
    {"url": "https://www.perplexity.ai/hub",                                     "company": "Perplexity",        "website": "Perplexity Hub"},
    {"url": "https://suno.com/blog",                                             "company": "Suno",              "website": "Suno Blog"},
    {"url": "https://runwayml.com/news",                                         "company": "Runway",            "website": "Runway AI News"},
    # ── Dev Tools / Coding ────────────────────────────────────────────────
    {"url": "https://cursor.com/blog",                                           "company": "Cursor",            "website": "Cursor Blog"},
    {"url": "https://windsurf.com/blog",                                         "company": "Windsurf",          "website": "Windsurf Blog"},
    {"url": "https://replit.com/news",                                           "company": "Replit",            "website": "Replit News"},
    {"url": "https://lovable.dev/blog?category=announcements",                   "company": "Lovable",           "website": "Lovable Blog"},
    {"url": "https://bolt.new/blog",                                             "company": "Bolt",              "website": "Bolt Blog"},
    {"url": "https://github.blog/ai-and-ml/github-copilot/",                     "company": "GitHub",            "website": "GitHub Blog - Copilot",      "rss_url": "https://github.blog/feed/"},
    {"url": "https://vercel.com/blog/category/v0",                               "company": "Vercel",            "website": "v0 Blog"},
    {"url": "https://www.helicone.ai/blog",                                      "company": "Helicone",          "website": "Helicone Blog"},
    # ── Productivity / Business Tools ─────────────────────────────────────
    {"url": "https://www.notion.com/releases",                                   "company": "Notion",            "website": "Notion Releases"},
    {"url": "https://www.figma.com/newsroom/",                                   "company": "Figma",             "website": "Figma Newsroom"},
    {"url": "https://asana.com/press/releases",                                  "company": "Asana",             "website": "Asana Press"},
    {"url": "https://zapier.com/blog/categories/company-news/",                  "company": "Zapier",            "website": "Zapier Blog"},
    {"url": "https://www.canva.com/newsroom/news/",                              "company": "Canva",             "website": "Canva Newsroom"},
    {"url": "https://www.atlasai.co/blog",                                       "company": "Atlas AI",          "website": "Atlas AI Blog"},
    {"url": "https://gamma.app/docs/News-AI-gbhs88u05m29s0p?mode=doc",           "company": "Gamma",             "website": "Gamma News"},
    # ── AI Infrastructure / Data ──────────────────────────────────────────
    {"url": "https://www.datadoghq.com/blog/ai/",                                "company": "Datadog",           "website": "Datadog AI Blog"},
    {"url": "https://blog.langchain.com/",                                       "company": "LangChain",         "website": "LangChain Blog",             "rss_url": "https://blog.langchain.com/rss/"},
    {"url": "https://www.pinecone.io/newsroom/",                                 "company": "Pinecone",          "website": "Pinecone Newsroom"},
    {"url": "https://pr.tsmc.com/english/latest-news?field_category_target_id=All", "company": "TSMC",           "website": "TSMC News"},
    {"url": "https://blog.n8n.io/tag/news/",                                     "company": "n8n",               "website": "n8n Blog"},
    {"url": "https://pipedream.com/docs/changelog",                              "company": "Pipedream",         "website": "Pipedream Changelog"},
    {"url": "https://www.bardeen.ai/release-notes",                              "company": "Bardeen",           "website": "Bardeen Release Notes"},
    {"url": "https://flowith.io/blog/#news",                                     "company": "Flowith",           "website": "Flowith Blog"},
    {"url": "https://blog.assisterr.ai/latest-news/",                            "company": "Assisterr AI",      "website": "Assisterr AI Blog"},
    {"url": "https://base44.com/changelog",                                      "company": "Base44",            "website": "Base44 Changelog"},
    {"url": "https://www.jasper.ai/press",                                       "company": "Jasper",            "website": "Jasper Press"},
    # ── Creative / Media AI ───────────────────────────────────────────────
    {"url": "https://artlist.io/blog/",                                          "company": "Artlist",           "website": "Artlist Blog"},
    {"url": "https://higgsfield.ai/blog",                                        "company": "Higgsfield",        "website": "Higgsfield Blog"},
    {"url": "https://www.descript.com/blog/category/product-updates",            "company": "Descript",          "website": "Descript Blog"},
    {"url": "https://gumroad.com/blog",                                          "company": "Gumroad",           "website": "Gumroad Blog"},
    {"url": "https://wisprflow.ai/blog",                                         "company": "Wispr Flow",        "website": "Wispr Flow Blog"},
    # ── News / Media ──────────────────────────────────────────────────────
    {"url": "https://www.reuters.com/technology/deepseek/",                       "company": "Various",           "website": "Reuters DeepSeek"},
    {"url": "https://www.reuters.com/technology/artificial-intelligence/",        "company": "Various",           "website": "Reuters AI"},
    {"url": "https://www.artificialintelligence-news.com/",                       "company": "Various",           "website": "AI News"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/",           "company": "Various",           "website": "TechCrunch AI",             "rss_url": "https://techcrunch.com/feed/"},
    {"url": "https://www.wsj.com/tech/ai",                                        "company": "Various",           "website": "WSJ AI"},
    {"url": "https://www.wsj.com/tech?mod=breadcrumb",                            "company": "Various",           "website": "WSJ Technology"},
    {"url": "https://edition.cnn.com/business/tech",                              "company": "Various",           "website": "CNN Business Tech"},
    {"url": "https://www.theverge.com/tech",                                      "company": "Various",           "website": "The Verge Tech",            "rss_url": "https://www.theverge.com/rss/index.xml"},
    {"url": "https://www.bloomberg.com/ai",                                       "company": "Various",           "website": "Bloomberg AI"},
    {"url": "https://news.mit.edu/topic/artificial-intelligence2",                "company": "Various",           "website": "MIT News AI",               "rss_url": "https://news.mit.edu/rss/topic/artificial-intelligence"},
    {"url": "https://spectrum.ieee.org/topic/artificial-intelligence/",           "company": "Various",           "website": "IEEE Spectrum AI"},
    {"url": "https://www.latest.com/category/ai",                                 "company": "Various",           "website": "Latest AI"},
    {"url": "https://www.haystack.tv/tag/ai",                                     "company": "Various",           "website": "Haystack AI"},
    {"url": "https://capacityglobal.com/news/",                                   "company": "Capacity",          "website": "Capacity News"},
    {"url": "https://skew-ai.com/",                                               "company": "Various",           "website": "Skew AI"},
    {"url": "https://futuretools.io/news",                                        "company": "Various",           "website": "Future Tools AI"},
]


# ─────────────────────── Date utilities ──────────────────────────────────────

def parse_date_to_iso(text):
    """Convert various date strings to YYYY-MM-DD. Returns '' on failure."""
    if not text:
        return ''
    t = str(text).strip()
    today = date.today()

    # Already ISO
    m = re.match(r'^(\d{4}-\d{2}-\d{2})', t)
    if m:
        return m.group(1)

    # Relative
    tl = t.lower()
    if any(x in tl for x in ['today', 'just now', 'moments ago']):
        return today.isoformat()
    m = re.search(r'(\d+)\s*(minute|min|second|sec)s?\s*ago', tl)
    if m:
        return today.isoformat()
    m = re.search(r'(\d+)\s*hours?\s*ago', tl)
    if m:
        return today.isoformat()
    m = re.search(r'(\d+)\s*days?\s*ago', tl)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()

    # Strip timezone suffixes for parsing
    clean = re.sub(r'T.*$', '', t).strip()
    clean = re.sub(r'\s+', ' ', clean)

    for fmt in ('%B %d, %Y', '%b %d, %Y', '%B %d %Y', '%b %d %Y',
                '%d %B %Y', '%d %b %Y', '%m/%d/%Y', '%d/%m/%Y',
                '%Y/%m/%d', '%B %Y', '%b %Y'):
        try:
            return datetime.strptime(clean, fmt).date().isoformat()
        except ValueError:
            pass
    return ''


def is_recent(text, days=3):
    """Return True if the date text is within the last `days` days."""
    if not text:
        return False
    tl = str(text).lower().strip()
    today = date.today()
    cutoff = today - timedelta(days=days - 1)

    # Relative expressions always qualify (within today)
    if any(x in tl for x in ['today', 'just now', 'moments ago', 'an hour ago']):
        return True
    if re.search(r'\d+\s*(minute|min|second|sec)s?\s*ago', tl):
        return True
    if re.search(r'\d+\s*hours?\s*ago', tl):
        return True
    m = re.search(r'(\d+)\s*days?\s*ago', tl)
    if m and int(m.group(1)) <= days:
        return True

    iso = parse_date_to_iso(text)
    if iso:
        return cutoff.isoformat() <= iso <= today.isoformat()
    return False


def url_has_recent(url, days=3):
    """Return True if the URL path encodes a date within the last days."""
    today = date.today()
    for i in range(days):
        d = today - timedelta(days=i)
        patterns = [
            f'/{d.year}/{d.month:02d}/{d.day:02d}',
            f'/{d.year}/{d.month}/{d.day}',
            f'/{d.isoformat()}',
            f'/{d.strftime("%Y%m%d")}',
            f'/{d.month:02d}/{d.day:02d}/{d.year}',
        ]
        if any(p in url for p in patterns):
            return True
    return False


# ─────────────────────── Text utilities ──────────────────────────────────────

def summarize(text, max_words=120):
    if not text:
        return ''
    words = text.split()
    result = ' '.join(words[:max_words])
    if len(words) > max_words:
        result += '...'
    return result


def extract_keywords(text, n=5):
    if not text:
        return []
    translator = str.maketrans('', '', string.punctuation)
    words = text.lower().translate(translator).split()
    words = [w for w in words if w not in STOP_WORDS and len(w) > 3 and w.isalpha()]
    if not words:
        return []
    return [w for w, _ in Counter(words).most_common(n)]


def extract_primary_company(title, text=''):
    """Try to identify the primary company from article title / body."""
    combined = (title or '') + ' ' + (text or '')[:800]
    for company in KNOWN_COMPANIES:
        if re.search(r'\b' + re.escape(company) + r'\b', combined, re.I):
            return company
    return ''


# ─────────────────────── HTTP / metadata helpers ─────────────────────────────

def fetch_page(url, timeout=20):
    """Fetch URL and return (BeautifulSoup, final_url)."""
    try:
        r = req_lib.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return BeautifulSoup(r.text, 'lxml'), r.url
    except Exception as e:
        logger.debug(f"fetch_page {url}: {e}")
        return None, url


def extract_meta_date(soup):
    """Extract publication date from article page meta tags / JSON-LD."""
    # Meta tag attributes to check
    for attr, val in [
        ('property', 'article:published_time'),
        ('property', 'article:modified_time'),
        ('property', 'og:updated_time'),
        ('name', 'pubdate'),
        ('name', 'publish-date'),
        ('name', 'publishdate'),
        ('name', 'date'),
        ('name', 'DC.date.issued'),
        ('name', 'DC.Date'),
        ('itemprop', 'datePublished'),
        ('itemprop', 'dateCreated'),
    ]:
        el = soup.find('meta', attrs={attr: val})
        if el and el.get('content'):
            return el['content']

    # JSON-LD
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            items = data if isinstance(data, list) else data.get('@graph', [data])
            for item in items:
                if isinstance(item, dict):
                    d = (item.get('datePublished') or item.get('dateCreated')
                         or item.get('dateModified'))
                    if d:
                        return d
        except Exception:
            pass

    # <time> elements — prefer datetime attr, then text
    for time_el in soup.find_all('time'):
        val = time_el.get('datetime', '')
        if val:
            return val
        txt = time_el.get_text(strip=True)
        if txt and len(txt) > 4:
            return txt

    # itemprop datePublished on any element
    for el in soup.find_all(attrs={'itemprop': re.compile(r'datePublish|dateCreat', re.I)}):
        v = el.get('content', '') or el.get('datetime', '') or el.get_text(strip=True)
        if v:
            return v

    return ''


def extract_meta_image(soup):
    """Extract thumbnail image URL from page meta tags / JSON-LD."""
    for attr, val in [
        ('property', 'og:image'),
        ('name', 'twitter:image'),
        ('property', 'og:image:url'),
        ('name', 'twitter:image:src'),
    ]:
        el = soup.find('meta', attrs={attr: val})
        if el and el.get('content') and el['content'].startswith('http'):
            return el['content']

    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            items = data if isinstance(data, list) else data.get('@graph', [data])
            for item in items:
                if not isinstance(item, dict):
                    continue
                img = item.get('image')
                if isinstance(img, str) and img.startswith('http'):
                    return img
                if isinstance(img, dict):
                    u = img.get('url', '')
                    if u.startswith('http'):
                        return u
                if isinstance(img, list) and img:
                    first = img[0]
                    if isinstance(first, str) and first.startswith('http'):
                        return first
                    if isinstance(first, dict):
                        u = first.get('url', '')
                        if u.startswith('http'):
                            return u
        except Exception:
            pass
    return ''


def fetch_article(url):
    """Fetch article page; return dict with text, date_iso, image_url."""
    result = {'text': '', 'date_iso': '', 'image_url': ''}
    try:
        r = req_lib.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        r.raise_for_status()
        html = r.text
        soup = BeautifulSoup(html, 'lxml')

        raw_date = extract_meta_date(soup)
        result['date_iso'] = parse_date_to_iso(raw_date)
        result['image_url'] = extract_meta_image(soup)

        # Text extraction
        if HAS_TRAFILATURA:
            text = trafilatura.extract(
                html, include_comments=False, include_tables=False, no_fallback=False
            )
            if text and len(text.split()) > 30:
                result['text'] = text
                return result

        # BeautifulSoup fallback
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            tag.decompose()
        for sel in ['article', 'main', '[class*="post-content"]',
                    '[class*="article-body"]', '[class*="content"]', 'body']:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(separator=' ', strip=True)
                if len(t.split()) > 30:
                    result['text'] = t
                    break
    except Exception as e:
        logger.debug(f"fetch_article {url}: {e}")
    return result


# ─────────────────────── RSS feed parser ─────────────────────────────────────

def fetch_rss(rss_url, days=3):
    """Fetch an RSS/Atom feed and return recent article metas."""
    results = []
    try:
        r = req_lib.get(rss_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        # Try XML parser; fall back to html.parser for malformed feeds
        try:
            soup = BeautifulSoup(r.content, 'xml')
        except Exception:
            soup = BeautifulSoup(r.content, 'html.parser')

        items = soup.find_all('item') or soup.find_all('entry')
        for item in items[:30]:
            title_el = item.find('title')
            # RSS uses <link>, Atom uses <link href="..."> or text content
            link_el = item.find('link')
            link = ''
            if link_el:
                link = link_el.get('href', '') or link_el.get_text(strip=True)
            if not link:
                guid = item.find('guid')
                if guid:
                    link = guid.get_text(strip=True)

            date_el = (item.find('pubDate') or item.find('published')
                       or item.find('updated') or item.find('dc:date'))
            date_text = date_el.get_text(strip=True) if date_el else ''

            if not is_recent(date_text, days):
                continue

            title = title_el.get_text(strip=True) if title_el else ''
            if not title or not link:
                continue

            # Image: enclosure or media:content or media:thumbnail
            img_url = ''
            enc = item.find('enclosure')
            if enc and (enc.get('type', '').startswith('image') or enc.get('url', '')):
                img_url = enc.get('url', '')
            if not img_url:
                media = item.find('media:content') or item.find('media:thumbnail')
                if media:
                    img_url = media.get('url', '')

            # Description/teaser — avoids fetching the full article page
            teaser = ''
            for tag in ['description', 'summary', 'content']:
                desc_el = item.find(tag)
                if desc_el:
                    raw = re.sub(r'<[^>]+>', ' ', desc_el.get_text(strip=True))
                    teaser = ' '.join(raw.split()[:120])
                    if teaser:
                        break

            results.append({'title': title, 'url': link,
                            'date_text': date_text, 'image_url': img_url,
                            'teaser': teaser})

    except Exception as e:
        logger.debug(f"fetch_rss {rss_url}: {e}")
    return results


# ─────────────────────── Next.js data extractor ───────────────────────────────

def _walk_json_for_articles(obj, out, depth=0):
    """Recursively search a JSON tree for article-like dicts."""
    if depth > 9 or len(out) > 30:
        return
    if isinstance(obj, dict):
        has_title = any(k in obj for k in ('title', 'headline', 'name', 'heading'))
        has_ref   = any(k in obj for k in ('publishedAt', 'date', 'createdAt',
                                            'publish_date', 'datePublished',
                                            'slug', 'url', 'href', 'link'))
        if has_title and has_ref:
            out.append(obj)
        for v in obj.values():
            _walk_json_for_articles(v, out, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json_for_articles(item, out, depth + 1)


def extract_nextjs_articles(soup, base_url, days=3):
    """Extract articles from __NEXT_DATA__ (Next.js SSR) embedded in the page."""
    script = soup.find('script', id='__NEXT_DATA__')
    if not script or not script.string:
        return []
    try:
        data = json.loads(script.string)
    except Exception:
        return []

    candidates = []
    _walk_json_for_articles(data, candidates)

    results, seen = [], set()
    for item in candidates:
        title = (item.get('title') or item.get('headline') or
                 item.get('name') or item.get('heading', ''))
        link  = (item.get('url') or item.get('href') or item.get('link') or
                 item.get('path', ''))
        slug  = item.get('slug', '')
        date  = (item.get('publishedAt') or item.get('date') or
                 item.get('createdAt') or item.get('publish_date') or
                 item.get('datePublished', ''))
        img   = item.get('image') or item.get('thumbnail') or item.get('featuredImage', '')
        if isinstance(img, dict):
            img = img.get('url', '') or img.get('src', '')

        if not title:
            continue
        if date and not is_recent(str(date), days):
            continue
        if not link and slug:
            link = urljoin(base_url, slug)
        if link and not link.startswith('http'):
            link = urljoin(base_url, link)
        if not link:
            continue
        img_str = str(img) if img and str(img).startswith('http') else ''
        if link not in seen:
            seen.add(link)
            results.append({'title': str(title), 'url': link,
                            'date_text': str(date) if date else '',
                            'image_url': img_str})
    return results[:5]


# ─────────────────────── Blog-post link fallback ──────────────────────────────

def find_article_links_fallback(soup, base_url, max_per_source=5, days=3):
    """
    When date-based strategies find nothing: collect slug-like links from the
    page and verify their dates by fetching each article individually.
    """
    domain = urlparse(base_url).netloc
    skip = ('/category/', '/tag/', '/topics/', '/author/', '/page/', '/feed',
            '/rss', '/about', '/contact', '/privacy', '/terms', '/search',
            '?s=', '#', '/cdn-cgi/', '/wp-login', '/wp-admin')

    candidates, seen = [], set()
    for a in soup.find_all('a', href=True):
        if len(candidates) >= max_per_source * 4:
            break
        href = a['href']
        if not href.startswith('http'):
            href = urljoin(base_url, href)

        ldom = urlparse(href).netloc
        if domain not in ldom and ldom not in domain:
            continue

        path = urlparse(href).path
        if not path or path == '/':
            continue
        if any(p in href for p in skip):
            continue

        parts = [p for p in path.strip('/').split('/') if p]
        if not parts:
            continue
        last = parts[-1]
        # Must look like a slug: contains hyphens, reasonable length
        if '-' not in last or len(last) < 8:
            continue

        title = a.get_text(strip=True)
        if not title or len(title) < 15:
            continue

        if href not in seen:
            seen.add(href)
            candidates.append({'url': href, 'title': title})

    if not candidates:
        return []

    confirmed = []
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            fmap = {pool.submit(fetch_article, c['url']): c
                    for c in candidates[:max_per_source * 3]}
            for future in as_completed(fmap, timeout=50):
                c = fmap[future]
                try:
                    art = future.result(timeout=20)
                    d_iso = art.get('date_iso', '')
                    # Accept if date is recent, OR date is empty (undated blog pages)
                    if is_recent(d_iso, days) or not d_iso:
                        c['_article'] = art
                        confirmed.append(c)
                except Exception:
                    pass
                if len(confirmed) >= max_per_source:
                    break
    except Exception:
        pass

    results = []
    for c in confirmed[:max_per_source]:
        art = c.get('_article', {})
        results.append({'title': c['title'], 'url': c['url'],
                        'date_text': art.get('date_iso', ''),
                        'image_url': art.get('image_url', ''),
                        '_article': art})
    return results


# ─────────────────────── Listing page parser ─────────────────────────────────

def get_date_text_from_element(el):
    for attr in ['datetime', 'data-date', 'data-published', 'content']:
        v = el.get(attr, '')
        if v:
            return v
    time_el = el.find('time')
    if time_el:
        return time_el.get('datetime', '') or time_el.get_text()
    date_el = el.find(class_=re.compile(r'date|time|publish|posted|when|ago', re.I))
    if date_el:
        return date_el.get_text()
    return ''


def find_recent_articles_in_soup(soup, base_url, max_per_source=5, days=3):
    """Return list of {title, url, date_text, image_url} for articles in last days."""
    results, seen = [], set()

    # ── Next.js __NEXT_DATA__ (React/Next.js sites) ───────────────────────
    nextjs = extract_nextjs_articles(soup, base_url, days)
    for item in nextjs:
        if item['url'] not in seen:
            seen.add(item['url'])
            results.append(item)
    if len(results) >= max_per_source:
        return results[:max_per_source]

    # ── JSON-LD ──────────────────────────────────────────────────────────
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            items = data if isinstance(data, list) else data.get('@graph', [data])
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get('@type') not in (
                    'NewsArticle', 'Article', 'BlogPosting', 'WebPage', 'TechArticle'
                ):
                    continue
                date_pub = item.get('datePublished', '') or item.get('dateModified', '')
                if not is_recent(date_pub, days):
                    continue
                art_url = (item.get('url', '')
                           or item.get('mainEntityOfPage', {}).get('@id', ''))
                title = item.get('headline', '') or item.get('name', '')
                img = item.get('image', {})
                img_url = (img.get('url', '') if isinstance(img, dict)
                           else img if isinstance(img, str) else '')
                if art_url and art_url not in seen and title:
                    seen.add(art_url)
                    results.append({'title': title, 'url': art_url,
                                    'date_text': date_pub, 'image_url': img_url})
        except Exception:
            pass

    if len(results) >= max_per_source:
        return results[:max_per_source]

    # ── Article-card containers ───────────────────────────────────────────
    containers = soup.find_all(
        ['article', 'div', 'li', 'section'],
        class_=re.compile(r'post|article|news|story|entry|item|card|feed|blog', re.I),
    )
    for container in containers:
        if len(results) >= max_per_source:
            break
        date_text = get_date_text_from_element(container)
        if not date_text:
            time_el = container.find('time')
            if time_el:
                date_text = time_el.get('datetime', '') or time_el.get_text()
        if not is_recent(date_text, days):
            continue
        link = container.find('a', href=True)
        title_el = container.find(re.compile(r'^h[1-6]$'))
        img_el = container.find('img')
        img_url = ''
        if img_el:
            img_url = (img_el.get('src', '') or img_el.get('data-src', '')
                       or img_el.get('data-lazy-src', ''))
            if img_url and not img_url.startswith('http'):
                img_url = urljoin(base_url, img_url)
        if not link or not title_el:
            continue
        href = link['href']
        if not href.startswith('http'):
            href = urljoin(base_url, href)
        title = title_el.get_text(strip=True)
        # Extract teaser/excerpt from the card to avoid fetching the full article
        teaser = ''
        teaser_el = (container.find(class_=re.compile(
                         r'excerpt|description|summary|teaser|intro|dek|standfirst|lede', re.I))
                     or container.find('p'))
        if teaser_el:
            t = teaser_el.get_text(strip=True)
            if len(t.split()) > 5:
                teaser = ' '.join(t.split()[:120])
        if href not in seen and title:
            seen.add(href)
            results.append({'title': title, 'url': href,
                            'date_text': date_text, 'image_url': img_url,
                            'teaser': teaser})

    if len(results) >= max_per_source:
        return results[:max_per_source]

    # ── Standalone <time> elements ────────────────────────────────────────
    for time_el in soup.find_all('time'):
        if len(results) >= max_per_source:
            break
        date_text = time_el.get('datetime', '') or time_el.get_text()
        if not is_recent(date_text, days):
            continue
        parent = time_el.parent
        for _ in range(6):
            if parent is None:
                break
            link = parent.find('a', href=True)
            heading = parent.find(re.compile(r'^h[1-6]$'))
            if link and heading:
                href = link['href']
                if not href.startswith('http'):
                    href = urljoin(base_url, href)
                title = heading.get_text(strip=True)
                if href not in seen and title:
                    seen.add(href)
                    results.append({'title': title, 'url': href,
                                    'date_text': date_text, 'image_url': ''})
                break
            parent = parent.parent

    if len(results) >= max_per_source:
        return results[:max_per_source]

    # ── URL date pattern ──────────────────────────────────────────────────
    for a in soup.find_all('a', href=True):
        if len(results) >= max_per_source:
            break
        href = a['href']
        if not href.startswith('http'):
            href = urljoin(base_url, href)
        if url_has_recent(href, days):
            title = a.get_text(strip=True)
            if href not in seen and title and len(title) > 10:
                seen.add(href)
                results.append({'title': title, 'url': href,
                                'date_text': '', 'image_url': ''})

    return results[:max_per_source]


# ─────────────────────── Source processor ────────────────────────────────────

def resolve_share_url(share_url):
    try:
        r = req_lib.get(share_url, headers=HEADERS, timeout=15, allow_redirects=True)
        u = r.url
        return u if 'google.com' not in u else None
    except Exception:
        return None


def process_source(source, days=3):
    company_cfg = source.get('company', 'Various')
    website = source.get('website', 'Unknown')
    page_url = source.get('url')
    share_url = source.get('share_url', '')

    if not page_url and share_url:
        resolved = resolve_share_url(share_url)
        if resolved:
            page_url = resolved
            domain = urlparse(resolved).netloc.replace('www.', '')
            if company_cfg in ('Various', 'Unknown'):
                company_cfg = domain.split('.')[0].title()
            if website in ('Unknown', 'Unknown Blog', 'Company News',
                           'Conversational AI', 'Conversational AI Platform'):
                website = domain

    if not page_url:
        return []

    # ── Try RSS feed first (most reliable for dates & content) ───────────
    articles_meta = []
    rss_url = source.get('rss_url', '')
    if rss_url:
        articles_meta = fetch_rss(rss_url, days)

    # ── Fall back to HTML scraping ────────────────────────────────────────
    soup, final_url = None, page_url
    if not articles_meta:
        soup, final_url = fetch_page(page_url)
        if soup:
            articles_meta = find_recent_articles_in_soup(soup, final_url,
                                                         max_per_source=5, days=days)

    # ── Last resort: scan all slug-like links on the page ─────────────────
    if not articles_meta and soup:
        articles_meta = find_article_links_fallback(soup, final_url,
                                                    max_per_source=5, days=days)

    results = []
    for meta in articles_meta:
        art_url = meta['url']
        art_title = meta['title']
        if not art_url or not art_title:
            continue

        # Priority: pre-fetched data > listing teaser > full article fetch
        # Using teaser avoids an extra HTTP request per article (much faster)
        if meta.get('_article'):
            article = meta['_article']
        elif meta.get('teaser'):
            article = {
                'text': meta['teaser'],
                'date_iso': parse_date_to_iso(meta.get('date_text', '')),
                'image_url': meta.get('image_url', ''),
            }
        else:
            article = fetch_article(art_url)
        text = article['text'] or art_title

        # Date: prefer article page metadata, fall back to listing date_text
        date_iso = article['date_iso']
        if not date_iso:
            date_iso = parse_date_to_iso(meta.get('date_text', ''))

        # Image: prefer article page og:image, fall back to listing thumbnail
        image_url = article['image_url'] or meta.get('image_url', '')

        summary = summarize(text, 120)
        keywords = extract_keywords(text, 5)

        # Resolve "Various" company to the actual company mentioned
        company = company_cfg
        if company in ('Various', 'Unknown', ''):
            detected = extract_primary_company(art_title, text)
            if detected:
                company = detected
            else:
                company = website  # last resort: use the website name

        results.append({
            'company': company,
            'website': website,
            'link': art_url,
            'title': art_title,
            'summary': summary,
            'keywords': keywords,
            'date_iso': date_iso,
            'image_url': image_url,
        })

    return results


# ─────────────────────── HTML template ───────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI News Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #fff;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
  font-size: 13px;
  color: #222;
  padding: 22px 26px;
}
h1 { font-size: 19px; font-weight: 700; color: #111; margin-bottom: 3px; }
.subtitle { font-size: 12px; color: #777; margin-bottom: 16px; }

/* Controls */
.toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
}
.refresh-btn {
  padding: 7px 16px;
  background: #1a73e8;
  color: #fff;
  border: none;
  border-radius: 5px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}
.refresh-btn:hover:not(:disabled) { background: #1558c0; }
.refresh-btn:disabled { background: #93b8f5; cursor: not-allowed; }
.filter-group {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.filter-group label { font-size: 12px; color: #555; font-weight: 500; }
.filter-group select,
.filter-group input[type="text"] {
  padding: 5px 8px;
  border: 1px solid #ccc;
  border-radius: 4px;
  font-size: 12px;
  background: #fff;
  outline: none;
}
.filter-group select:focus,
.filter-group input:focus { border-color: #1a73e8; }
#status { font-size: 12px; color: #555; margin-left: auto; }
#status.loading { color: #1a73e8; }
#status.error   { color: #c0392b; }

/* Table */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
thead th {
  background: #f5f6f7;
  border: 1px solid #d8d8d8;
  padding: 9px 10px;
  text-align: left;
  font-weight: 700;
  color: #333;
  white-space: nowrap;
  position: sticky;
  top: 0;
  z-index: 2;
  user-select: none;
}
thead th.sortable { cursor: pointer; }
thead th.sortable:hover { background: #eaecef; }
.sort-icon { margin-left: 4px; opacity: .5; font-size: 10px; }
.sort-icon.active { opacity: 1; color: #1a73e8; }
tbody td {
  border: 1px solid #e6e6e6;
  padding: 8px 10px;
  vertical-align: top;
  line-height: 1.5;
}
tbody tr:nth-child(even) td { background: #fafafa; }
tbody tr:hover td { background: #eef4ff; }

/* Column widths */
.col-date     { width: 92px; white-space: nowrap; color: #333; font-size: 12px; font-weight: 500; }
.col-company  { width: 115px; font-weight: 600; color: #111; }
.col-website  { width: 140px; color: #444; }
.col-source   { width: 68px; text-align: center; }
.col-summary  { width: 220px; max-width: 240px; word-wrap: break-word; overflow-wrap: break-word; white-space: normal; }
.col-keywords { width: 175px; }
.col-image    { width: 96px; text-align: center; }

.art-title  { font-weight: 600; color: #111; margin-bottom: 5px; font-size: 12.5px; }
.art-summary{ color: #555; word-wrap: break-word; overflow-wrap: break-word; line-height: 1.45; }
.src-link {
  display: inline-block;
  padding: 3px 9px;
  background: #e8f0fe;
  color: #1a73e8;
  text-decoration: none;
  border-radius: 4px;
  font-size: 11.5px;
  font-weight: 600;
}
.src-link:hover { background: #d2e3fc; }
.kw {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 3px;
  font-size: 11px;
  margin: 2px 2px 2px 0;
  font-weight: 500;
}
.thumb {
  max-width: 84px;
  max-height: 64px;
  object-fit: cover;
  border-radius: 4px;
  border: 1px solid #e0e0e0;
}
.empty { text-align: center; padding: 50px; color: #aaa; font-size: 13px; }
.spinner {
  display: inline-block;
  width: 12px; height: 12px;
  border: 2px solid #1a73e8;
  border-radius: 50%;
  border-top-color: transparent;
  animation: spin .7s linear infinite;
  vertical-align: middle;
  margin-right: 5px;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<h1>AI News Dashboard</h1>
<p class="subtitle" id="today-label"></p>

<div class="toolbar">
  <button class="refresh-btn" id="refresh-btn" onclick="fetchNews(true)">&#8635; Refresh</button>

  <div class="filter-group">
    <label for="f-days">Period:</label>
    <select id="f-days" onchange="applyFilters()">
      <option value="3" selected>Last 3 days</option>
      <option value="2">Last 2 days</option>
      <option value="1">Today only</option>
    </select>
  </div>

  <div class="filter-group">
    <label for="f-website">Website:</label>
    <select id="f-website" onchange="applyFilters()">
      <option value="">All websites</option>
    </select>
  </div>

  <div class="filter-group">
    <label for="f-keyword">Keyword:</label>
    <input id="f-keyword" type="text" placeholder="Filter by keyword…" oninput="applyFilters()" style="width:160px">
  </div>

  <span id="status">Auto-loading news…</span>
</div>

<div class="table-wrap">
<table id="tbl">
  <thead>
    <tr>
      <th class="col-date sortable" onclick="sortBy('date_iso')">
        Date <span class="sort-icon active" id="si-date_iso">▾</span>
      </th>
      <th class="col-company sortable" onclick="sortBy('company')">
        Company <span class="sort-icon" id="si-company"></span>
      </th>
      <th class="col-website sortable" onclick="sortBy('website')">
        Website <span class="sort-icon" id="si-website"></span>
      </th>
      <th class="col-source">Source</th>
      <th class="col-summary">Title &amp; Summary</th>
      <th class="col-keywords">Keywords</th>
      <th class="col-image">Image</th>
    </tr>
  </thead>
  <tbody id="tbody">
    <tr><td colspan="7" class="empty"><span class="spinner"></span>Loading…</td></tr>
  </tbody>
</table>
</div>

<script>
// ── Keyword color palette (bg / text pairs) ──
const KW_PALETTE = [
  {bg:'#dbeafe', tx:'#1e40af'}, // blue
  {bg:'#dcfce7', tx:'#166534'}, // green
  {bg:'#fef9c3', tx:'#854d0e'}, // yellow
  {bg:'#fce7f3', tx:'#9d174d'}, // pink
  {bg:'#ede9fe', tx:'#5b21b6'}, // purple
  {bg:'#ffedd5', tx:'#9a3412'}, // orange
  {bg:'#cffafe', tx:'#155e75'}, // cyan
  {bg:'#f0fdf4', tx:'#14532d'}, // mint
  {bg:'#fff1f2', tx:'#9f1239'}, // rose
  {bg:'#faf5ff', tx:'#6b21a8'}, // violet
];
// Consistent color per keyword word (hash)
const kwColorMap = {};
function kwColor(word) {
  if (kwColorMap[word]) return kwColorMap[word];
  let h = 0;
  for (let c of word) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const color = KW_PALETTE[h % KW_PALETTE.length];
  kwColorMap[word] = color;
  return color;
}

// ── State ──
let allData = [];
let sortState = { col: 'date_iso', dir: 'desc' };
let filters   = { website: '', keyword: '', days: 3 };

const $ = id => document.getElementById(id);

function esc(s) {
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    const [y,m,d] = iso.split('-');
    const months = ['Jan','Feb','Mar','Apr','May','Jun',
                    'Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${months[+m-1]} ${+d}, ${y}`;
  } catch { return iso; }
}

function updateSortIcons() {
  ['date_iso','company','website'].forEach(col => {
    const el = $('si-' + col);
    if (!el) return;
    if (col === sortState.col) {
      el.textContent = sortState.dir === 'asc' ? '▴' : '▾';
      el.className = 'sort-icon active';
    } else {
      el.textContent = '⇅';
      el.className = 'sort-icon';
    }
  });
}

function render() {
  const daysAgo = parseInt(filters.days) || 3;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - daysAgo + 1);
  cutoff.setHours(0,0,0,0);
  const cutoffIso = cutoff.toISOString().slice(0,10);

  let filtered = allData.filter(a => {
    if (filters.website && a.website !== filters.website) return false;
    if (filters.keyword) {
      const kw = filters.keyword.toLowerCase();
      if (!(a.keywords||[]).some(k => k.toLowerCase().includes(kw))) return false;
    }
    if (a.date_iso && a.date_iso < cutoffIso) return false;
    return true;
  });

  filtered.sort((a, b) => {
    let va = String(a[sortState.col] || '').toLowerCase();
    let vb = String(b[sortState.col] || '').toLowerCase();
    if (va < vb) return sortState.dir === 'asc' ? -1 : 1;
    if (va > vb) return sortState.dir === 'asc' ?  1 : -1;
    return 0;
  });

  if (!filtered.length) {
    $('tbody').innerHTML = '<tr><td colspan="7" class="empty">No articles match the current filters.</td></tr>';
    $('status').textContent = '0 results';
    return;
  }

  $('tbody').innerHTML = filtered.map(a => {
    const kws = (a.keywords||[]).map(k => {
      const c = kwColor(k);
      return `<span class="kw" style="background:${c.bg};color:${c.tx}">${esc(k)}</span>`;
    }).join('');
    const img = a.image_url
      ? `<img class="thumb" src="${esc(a.image_url)}" loading="lazy" onerror="this.style.display='none'" alt="">`
      : '—';
    return `<tr>
      <td class="col-date">${fmtDate(a.date_iso)}</td>
      <td class="col-company">${esc(a.company)}</td>
      <td class="col-website">${esc(a.website)}</td>
      <td class="col-source"><a class="src-link" href="${esc(a.link)}" target="_blank" rel="noopener">Link ↗</a></td>
      <td class="col-summary">
        <div class="art-title">${esc(a.title)}</div>
        <div class="art-summary">${esc(a.summary)}</div>
      </td>
      <td class="col-keywords">${kws}</td>
      <td class="col-image">${img}</td>
    </tr>`;
  }).join('');

  $('status').textContent =
    `${filtered.length} article${filtered.length!==1?'s':''} shown`;
}

function sortBy(col) {
  if (sortState.col === col) {
    sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
  } else {
    sortState.col = col;
    sortState.dir = col === 'date_iso' ? 'desc' : 'asc';
  }
  updateSortIcons();
  render();
}

function applyFilters() {
  filters.website = $('f-website').value;
  filters.keyword = $('f-keyword').value.trim();
  filters.days    = parseInt($('f-days').value) || 3;
  render();
}

function populateWebsiteFilter() {
  const websites = [...new Set(allData.map(a => a.website))].sort();
  const sel = $('f-website');
  const cur = sel.value;
  sel.innerHTML = '<option value="">All websites</option>' +
    websites.map(w => `<option value="${esc(w)}"${w===cur?' selected':''}>${esc(w)}</option>`).join('');
}

function setStatus(msg, cls) {
  const el = $('status');
  el.className = cls || '';
  el.innerHTML = msg;
}

async function fetchNews(forceRefresh) {
  $('refresh-btn').disabled = true;
  setStatus('<span class="spinner"></span>Fetching from all sources… (may take 1–2 min)', 'loading');
  $('tbody').innerHTML = '<tr><td colspan="7" class="empty"><span class="spinner"></span>Loading…</td></tr>';

  try {
    const url = forceRefresh ? '/api/news?refresh=1' : '/api/news';
    const res = await fetch(url);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    allData = data.articles || [];

    const cached = data.from_cache ? ' (cached)' : '';
    const ts = data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : '';

    populateWebsiteFilter();
    applyFilters();
    setStatus(`${allData.length} total articles${cached} · ${ts}`);
  } catch(e) {
    setStatus('Error: ' + esc(e.message), 'error');
    $('tbody').innerHTML = `<tr><td colspan="7" class="empty">Failed: ${esc(e.message)}</td></tr>`;
  } finally {
    $('refresh-btn').disabled = false;
  }
}

// Init
const d = new Date();
$('today-label').textContent = d.toLocaleDateString('en-US', {
  weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
});
updateSortIcons();
fetchNews(false);
</script>
</body>
</html>"""


# ─────────────────────── Flask routes ────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/news')
def get_news():
    force = bool(request.args.get('refresh'))
    days = int(request.args.get('days', 3))

    if not force and CACHE['data'] is not None and CACHE['timestamp']:
        age = (datetime.now() - CACHE['timestamp']).total_seconds()
        if age < CACHE['ttl_seconds']:
            return jsonify({
                'articles': CACHE['data'],
                'from_cache': True,
                'timestamp': CACHE['timestamp'].isoformat(),
            })

    all_articles = []
    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(process_source, src, days): src for src in SOURCES}
        for future in as_completed(futures, timeout=180):
            src = futures[future]
            try:
                all_articles.extend(future.result(timeout=30))
            except Exception as e:
                logger.debug(f"Source {src.get('website','?')} failed: {e}")

    # Default sort: newest first
    all_articles.sort(key=lambda x: x.get('date_iso', ''), reverse=True)

    now = datetime.now()
    CACHE['data'] = all_articles
    CACHE['timestamp'] = now

    return jsonify({
        'articles': all_articles,
        'from_cache': False,
        'timestamp': now.isoformat(),
    })


if __name__ == '__main__':
    today = date.today()
    print("=" * 55)
    print("  AI News Dashboard  (last 3 days)")
    print(f"  Date range: {(today - timedelta(days=2)).isoformat()} → {today.isoformat()}")
    print("  Open: http://localhost:5000")
    print("=" * 55)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
