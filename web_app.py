import asyncio
import json
import os
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, flash, send_file
import pandas as pd

# The scraper class from the original script (simplified)
from playwright.async_api import async_playwright


def ensure_chromium_installed():
    try:
        import subprocess, sys
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    except Exception as e:
        print(f"Playwright chromium install failed: {e}")


class OliveYoungScraper:
    def __init__(self):
        self.base_url = "https://www.oliveyoung.co.kr/store/search/getSearchMain.do"

    async def scrape_products(self, search_keywords, max_pages=1):
        products = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            )
            page = await context.new_page()

            for keyword in search_keywords:
                for page_num in range(1, max_pages + 1):
                    url = f"{self.base_url}?query={keyword}&page={page_num}"
                    await page.goto(url, wait_until="networkidle")
                    await asyncio.sleep(1)
                    await self._extract_products_to_list(page, keyword, products)

            await browser.close()
        return products

    async def scrape_selected_products(self, selected_products):
        updated = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            for prod in selected_products:
                code = prod.get('상품코드')
                if not code:
                    updated.append(prod)
                    continue
                url = f"https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo={code}"
                try:
                    await page.goto(url, wait_until="networkidle")
                    await asyncio.sleep(1)
                    new = await self._extract_product_from_detail_page(page, prod)
                    updated.append(new)
                except Exception as e:
                    prod['상태'] = str(e)
                    updated.append(prod)
            await browser.close()
        return updated

    async def _extract_products_to_list(self, page, keyword, product_list):
        items = await page.query_selector_all("li.flag.li_result")
        for element in items:
            try:
                brand_elem = await element.query_selector(".tx_brand")
                name_elem = await element.query_selector(".tx_name")
                price_elem = await element.query_selector(".tx_cur .tx_num")
                href_elem = await element.query_selector(".prd_thumb")

                brand = await brand_elem.inner_text() if brand_elem else ""
                name = await name_elem.inner_text() if name_elem else ""
                price = await price_elem.inner_text() if price_elem else ""
                href = await href_elem.get_attribute("href") if href_elem else ""
                code = ""
                if href:
                    import re
                    m = re.search(r'goodsNo=([A-Z0-9]+)', href)
                    if m:
                        code = m.group(1)

                product_list.append({
                    '브랜드': brand,
                    '상품명': name,
                    '할인가': price,
                    '혜택': '',
                    '검색키워드': keyword,
                    '상품코드': code,
                    '크롤링시간': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
            except Exception:
                continue

    async def _extract_product_from_detail_page(self, page, original_product):
        await page.wait_for_load_state("networkidle")
        brand = original_product.get('브랜드', '')
        name = original_product.get('상품명', '')
        price = original_product.get('할인가', '')
        try:
            b = await page.query_selector(".prd_brand")
            if b:
                brand = (await b.inner_text()).strip()
        except Exception:
            pass
        try:
            n = await page.query_selector(".prd_name")
            if n:
                name = (await n.inner_text()).strip()
        except Exception:
            pass
        try:
            p_elem = await page.query_selector(".price .price-2 strong")
            if p_elem:
                price = (await p_elem.inner_text()).strip()
        except Exception:
            pass
        original_product.update({
            '브랜드': brand,
            '상품명': name,
            '할인가': price,
            '업데이트시간': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        return original_product


app = Flask(__name__)
app.secret_key = 'secret'

DATA_FILE = 'oliveyoung_data.json'
SCRAPER = OliveYoungScraper()
DATA = {'results': [], 'favorites': []}


def load_data():
    global DATA
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            DATA = json.load(f)


def save_data():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(DATA, f, ensure_ascii=False, indent=2)


@app.route('/', methods=['GET', 'POST'])
def index():
    results = DATA.get('results', [])
    if request.method == 'POST':
        keywords = request.form.get('keywords', '')
        pages = int(request.form.get('pages', '1'))
        keys = [k.strip() for k in keywords.split(',') if k.strip()]
        products = asyncio.run(SCRAPER.scrape_products(keys, pages))
        DATA['results'] = products
        save_data()
        results = products
        flash(f"총 {len(products)}개 상품")
    return render_template('index.html', results=results, title='검색')


@app.route('/add_favorites', methods=['POST'])
def add_favorites():
    indices = request.form.getlist('select')
    added = 0
    for idx in indices:
        prod = DATA['results'][int(idx)]
        if prod not in DATA['favorites']:
            DATA['favorites'].append(prod)
            added += 1
    save_data()
    flash(f"{added}개 상품을 관심상품에 추가했습니다")
    return redirect(url_for('favorites'))


@app.route('/favorites')
def favorites():
    return render_template('favorites.html', favorites=DATA.get('favorites', []), title='관심상품')


@app.route('/refresh_favorites', methods=['POST'])
def refresh_favorites():
    indices = [int(i) for i in request.form.getlist('select')]
    selected = [DATA['favorites'][i] for i in indices]
    updated = asyncio.run(SCRAPER.scrape_selected_products(selected))
    for idx, upd in zip(indices, updated):
        DATA['favorites'][idx] = upd
    save_data()
    flash('선택한 상품을 새로고침했습니다')
    return redirect(url_for('favorites'))


@app.route('/remove_favorites', methods=['POST'])
def remove_favorites():
    indices = sorted([int(i) for i in request.form.getlist('select')], reverse=True)
    for idx in indices:
        DATA['favorites'].pop(idx)
    save_data()
    flash('선택한 상품을 제거했습니다')
    return redirect(url_for('favorites'))


@app.route('/export_excel')
def export_excel():
    if not DATA.get('favorites'):
        flash('관심상품이 없습니다')
        return redirect(url_for('favorites'))
    df = pd.DataFrame(DATA['favorites'])
    from io import BytesIO
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='favorites.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


if __name__ == '__main__':
    ensure_chromium_installed()
    load_data()
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
