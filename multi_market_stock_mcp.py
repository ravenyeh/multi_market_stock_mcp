"""
全球股市即時分析 MCP 服務器
Multi-Market Stock Real-Time Analysis MCP Server

支援台灣股市、中國A股、美股的即時報價、技術分析、買賣建議等功能。
Supports Taiwan Stock, China A-Share, and US Stock markets.
"""

# ==================== 重要：必須在導入 MCP 之前應用 nest-asyncio ====================
# 這解決了 "Already running asyncio in this thread" 錯誤
# 該錯誤發生在 FastMCP 內部的 cli.py 嘗試在已有事件循環的環境中創建新循環
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    # 如果沒有安裝 nest-asyncio，打印警告但繼續執行
    import sys
    print("WARNING: nest-asyncio not installed. May encounter event loop issues.", file=sys.stderr)
    print("Install with: pip install nest-asyncio", file=sys.stderr)

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any, Literal
from enum import Enum
import httpx
import json
from datetime import datetime
import asyncio

# 初始化 MCP 服務器
mcp = FastMCP("multi_market_stock_mcp")

# 常數定義
CHARACTER_LIMIT = 25000
TWSE_API_BASE = "https://mis.twse.com.tw/stock/api"
REQUEST_DELAY = 3.0


# ==================== 市場類型 ====================

class MarketType(str, Enum):
    """市場類型"""
    TAIWAN = "taiwan"  # 台灣股市
    CHINA = "china"    # 中國A股
    US = "us"          # 美國股市
    AUTO = "auto"      # 自動識別


# ==================== 回應格式 ====================

class ResponseFormat(str, Enum):
    """輸出格式選項"""
    MARKDOWN = "markdown"
    JSON = "json"


# ==================== 輸入模型 ====================

class UniversalStockQueryInput(BaseModel):
    """通用股票查詢輸入模型"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra='forbid'
    )

    stock_code: str = Field(
        ...,
        description=(
            "股票代碼，例如："
            "台股: '2330', '0050' | "
            "A股: '600519'(茅台), '000001'(平安銀行), 'sh600519', 'sz000001' | "
            "美股: 'AAPL', 'TSLA', 'NVDA'"
        ),
        min_length=1,
        max_length=10
    )
    market: MarketType = Field(
        default=MarketType.AUTO,
        description="市場類型: 'taiwan'(台股), 'china'(A股), 'us'(美股), 'auto'(自動識別)"
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="輸出格式：'markdown' 適合人類閱讀，'json' 適合程式處理"
    )


class MultiMarketStockQueryInput(BaseModel):
    """多支股票查詢輸入模型（支援跨市場）"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra='forbid'
    )

    stock_codes: List[str] = Field(
        ...,
        description="股票代碼列表，可混合不同市場，例如：['2330', 'AAPL', '600519']",
        min_length=1,
        max_length=20
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="輸出格式"
    )


class UniversalTechnicalAnalysisInput(BaseModel):
    """通用技術分析輸入模型"""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra='forbid'
    )

    stock_code: str = Field(
        ...,
        description="股票代碼"
    )
    market: MarketType = Field(
        default=MarketType.AUTO,
        description="市場類型"
    )
    analysis_type: Literal["basic", "advanced", "full"] = Field(
        default="basic",
        description="分析類型"
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="輸出格式"
    )


# ==================== 市場識別 ====================

def detect_market(stock_code: str) -> MarketType:
    """
    自動識別股票代碼所屬市場

    Args:
        stock_code: 股票代碼

    Returns:
        MarketType: 市場類型
    """
    code = stock_code.upper().strip()

    # 美股特徵：字母開頭或包含字母
    if any(c.isalpha() for c in code):
        # 排除 sh/sz 開頭的A股代碼
        if code.startswith(('SH', 'SZ')) and len(code) == 8:
            return MarketType.CHINA
        return MarketType.US

    # 純數字代碼
    if code.isdigit():
        code_len = len(code)
        # 台股：4-6位數字
        if 4 <= code_len <= 6:
            return MarketType.TAIWAN
        # A股：6位數字
        elif code_len == 6:
            return MarketType.CHINA

    # 預設台股
    return MarketType.TAIWAN


# ==================== 台股 API ====================

async def fetch_taiwan_stock_data(stock_codes: List[str], exchange: str = "tse") -> Dict[str, Any]:
    """
    從台灣證交所 API 獲取股票即時資料

    Args:
        stock_codes: 股票代碼列表
        exchange: 交易所類型，'tse' 為上市，'otc' 為上櫃

    Returns:
        API 回應的 JSON 資料
    """
    stock_list = '|'.join(f'{exchange}_{code}.tw' for code in stock_codes)
    url = f"{TWSE_API_BASE}/getStockInfo.jsp"
    params = {
        'ex_ch': stock_list,
        'json': '1',
        'delay': '0'
    }

    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get('rtcode') != '0000':
                raise ValueError(f"API 回應錯誤: {data.get('rtmessage', 'Unknown error')}")

            return data
        except httpx.HTTPError as e:
            raise ValueError(f"無法連接證交所 API: {str(e)}")
        except json.JSONDecodeError:
            raise ValueError("API 回應格式錯誤")


async def get_taiwan_stock_quote(stock_code: str) -> Dict[str, Any]:
    """獲取台股即時報價"""
    try:
        # 嘗試上市
        data = await fetch_taiwan_stock_data([stock_code], "tse")
        if data.get('msgArray'):
            stock_info = data['msgArray'][0]
            stock_info['market'] = 'taiwan'
            stock_info['market_name'] = '台灣股市'
            return stock_info

        # 嘗試上櫃
        data = await fetch_taiwan_stock_data([stock_code], "otc")
        if data.get('msgArray'):
            stock_info = data['msgArray'][0]
            stock_info['market'] = 'taiwan'
            stock_info['market_name'] = '台灣股市'
            return stock_info

        raise ValueError(f"找不到股票代碼 {stock_code}")
    except Exception as e:
        raise ValueError(f"查詢台股失敗: {str(e)}")


# ==================== A股 API (使用 akshare) ====================

async def get_china_stock_quote(stock_code: str) -> Dict[str, Any]:
    """
    獲取A股即時報價
    使用騰訊財經接口（免費，無需API key）
    """
    try:
        # 處理股票代碼格式
        code = stock_code.upper().strip()

        # 如果已經有 sh/sz 前綴，移除
        if code.startswith(('SH', 'SZ')):
            code = code[2:]

        # 根據代碼判斷交易所
        # 60xxxx = 上海主板, 688xxx = 科創板
        # 00xxxx = 深圳主板, 30xxxx = 創業板
        if code.startswith(('60', '688', '689')):
            tencent_code = f'sh{code}'
        elif code.startswith(('00', '30', '002')):
            tencent_code = f'sz{code}'
        else:
            # 預設上海
            tencent_code = f'sh{code}'

        # 騰訊財經 API
        url = f"http://qt.gtimg.cn/q={tencent_code}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()

            # 解析回應（格式：v_sh603501="51~韋爾股份~603501~價格~..."）
            content = response.text
            if '="' not in content or content.strip() == '':
                raise ValueError(f"找不到股票代碼 {stock_code}")

            # 提取數據部分
            data_start = content.find('="') + 2
            data_end = content.rfind('"')
            data_str = content[data_start:data_end]

            if not data_str or data_str == '':
                raise ValueError(f"股票代碼 {stock_code} 無數據，可能非交易時段或代碼錯誤")

            parts = data_str.split('~')

            if len(parts) < 40:
                raise ValueError("數據格式錯誤或市場休市")

            # 構建標準化數據結構
            # 騰訊財經格式：
            # 0:未知 1:名稱 2:代碼 3:當前價 4:昨收 5:開盤 6:成交量(手) 7:外盤 8:內盤
            # 9-10:買一價量 11-12:賣一價量 13-14:買二價量 15-16:賣二價量
            # 17-18:買三價量 19-20:賣三價量 21-22:買四價量 23-24:賣四價量
            # 25-26:買五價量 27-28:賣五價量
            # 30:時間 31:漲跌額 32:漲跌% 33:最高 34:最低 37:成交額(萬)
            stock_info = {
                'market': 'china',
                'market_name': '中國A股',
                'c': tencent_code.upper(),  # 代碼
                'n': parts[1],  # 名稱
                'z': parts[3],  # 當前價
                'y': parts[4],  # 昨收價
                'o': parts[5],  # 開盤價
                'h': parts[33],  # 最高價
                'l': parts[34],  # 最低價
                'v': str(int(float(parts[6]) / 100)) if parts[6] else '0',  # 成交量（手轉張）
                'value': parts[37],  # 成交額(萬)
                # 買五檔 (價格)
                'b': '_'.join([parts[9], parts[13], parts[17], parts[21], parts[25]]),   # 買一到五價
                # 買五檔 (數量)
                'g': '_'.join([parts[10], parts[14], parts[18], parts[22], parts[26]]),  # 買一到五量
                # 賣五檔 (價格)
                'a': '_'.join([parts[11], parts[15], parts[19], parts[23], parts[27]]),  # 賣一到五價
                # 賣五檔 (數量)
                'f': '_'.join([parts[12], parts[16], parts[20], parts[24], parts[28]]),  # 賣一到五量
                'tlong': str(int(datetime.now().timestamp() * 1000)),  # 時間戳
                'date': parts[30] if len(parts) > 30 else '',  # 日期時間
                'time': parts[31] if len(parts) > 31 else ''   # 漲跌額
            }

            return stock_info

    except Exception as e:
        raise ValueError(f"查詢A股失敗: {str(e)}")


# ==================== 美股 API (使用 Yahoo Finance) ====================

async def get_us_stock_quote(stock_code: str) -> Dict[str, Any]:
    """
    獲取美股即時報價
    使用 Yahoo Finance API（免費）
    """
    try:
        symbol = stock_code.upper().strip()

        # Yahoo Finance v8 API
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {
            'interval': '1d',
            'range': '1d'
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if 'chart' not in data or 'result' not in data['chart']:
                raise ValueError(f"找不到股票代碼 {symbol}")

            result = data['chart']['result'][0]
            meta = result['meta']
            quote = result['indicators']['quote'][0]

            # 獲取當前價格
            current_price = meta.get('regularMarketPrice', 0)
            prev_close = meta.get('previousClose', meta.get('chartPreviousClose', 0))

            # 構建標準化數據結構
            stock_info = {
                'market': 'us',
                'market_name': '美國股市',
                'c': symbol,
                'n': meta.get('symbol', symbol),
                'longName': meta.get('longName', ''),
                'o': quote['open'][-1] if quote.get('open') else 0,
                'h': quote['high'][-1] if quote.get('high') else 0,
                'l': quote['low'][-1] if quote.get('low') else 0,
                'z': current_price,
                'y': prev_close,
                'v': str(int(quote['volume'][-1] / 100)) if quote.get('volume') else '0',
                'currency': meta.get('currency', 'USD'),
                'exchangeName': meta.get('exchangeName', 'NYSE'),
                'tlong': str(int(datetime.now().timestamp() * 1000))
            }

            return stock_info

    except Exception as e:
        raise ValueError(f"查詢美股失敗: {str(e)}")


# ==================== 通用查詢介面 ====================

async def get_stock_quote(stock_code: str, market: MarketType = MarketType.AUTO) -> Dict[str, Any]:
    """
    通用股票查詢介面，自動路由到對應市場

    Args:
        stock_code: 股票代碼
        market: 指定市場類型，或 AUTO 自動識別

    Returns:
        標準化的股票資訊
    """
    # 自動識別市場
    if market == MarketType.AUTO:
        market = detect_market(stock_code)

    # 路由到對應市場
    if market == MarketType.TAIWAN:
        return await get_taiwan_stock_quote(stock_code)
    elif market == MarketType.CHINA:
        return await get_china_stock_quote(stock_code)
    elif market == MarketType.US:
        return await get_us_stock_quote(stock_code)
    else:
        raise ValueError(f"不支援的市場類型: {market}")


# ==================== 工具函數 ====================

def calculate_price_change(current: float, previous: float) -> tuple[float, str]:
    """計算價格變動"""
    change = current - previous
    change_percent = (change / previous * 100) if previous > 0 else 0
    sign = "+" if change >= 0 else ""
    return change, f"{sign}{change_percent:.2f}%"


def format_timestamp(timestamp: str) -> str:
    """格式化時間戳記"""
    try:
        ts = int(timestamp) / 1000
        dt = datetime.fromtimestamp(ts)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return timestamp


def safe_float(value, default=0.0):
    """安全轉換浮點數"""
    if value in ['', '-', None]:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value, default=0):
    """安全轉換整數"""
    if value in ['', '-', None]:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


# ==================== 技術分析 ====================

def analyze_technical_indicators(stock_data: Dict[str, Any]) -> Dict[str, Any]:
    """技術指標分析（通用版本，支援所有市場）"""
    try:
        current = safe_float(stock_data.get('z'))
        open_price = safe_float(stock_data.get('o'))
        high = safe_float(stock_data.get('h'))
        low = safe_float(stock_data.get('l'))
        prev_close = safe_float(stock_data.get('y'))
        volume = safe_int(stock_data.get('v'))

        # 計算技術指標
        price_position = (current - low) / (high - low) * 100 if high > low else 50

        # 判斷趨勢
        if current > prev_close:
            trend = "上漲"
            trend_strength = "強勢" if current > open_price else "震盪上漲"
        elif current < prev_close:
            trend = "下跌"
            trend_strength = "弱勢" if current < open_price else "震盪下跌"
        else:
            trend = "平盤"
            trend_strength = "盤整"

        # 價格位置分析
        if price_position >= 80:
            position_desc = "高檔區（接近今日最高）"
        elif price_position >= 60:
            position_desc = "中高檔區"
        elif price_position >= 40:
            position_desc = "中檔區"
        elif price_position >= 20:
            position_desc = "中低檔區"
        else:
            position_desc = "低檔區（接近今日最低）"

        return {
            'trend': trend,
            'trend_strength': trend_strength,
            'price_position': price_position,
            'position_desc': position_desc,
            'volume': volume,
            'high': high,
            'low': low,
            'open': open_price,
            'current': current,
            'prev_close': prev_close
        }
    except Exception as e:
        return {'error': f"技術分析錯誤: {str(e)}"}


def generate_trading_suggestion(analysis: Dict[str, Any], stock_data: Dict[str, Any]) -> Dict[str, str]:
    """生成買賣建議（通用版本）"""
    if 'error' in analysis:
        return {
            'action': '無法分析',
            'reason': analysis['error'],
            'risk_level': '未知'
        }

    price_position = analysis['price_position']
    trend = analysis['trend']
    market = stock_data.get('market', 'unknown')

    # 決策邏輯
    if trend == "上漲":
        if price_position < 40:
            action = "買進"
            reason = f"股價處於{analysis['position_desc']}且呈上漲趨勢，具備向上動能"
            risk_level = "中等"
        elif price_position < 70:
            action = "觀望或小量買進"
            reason = f"股價已上漲至{analysis['position_desc']}，可等回檔再進場"
            risk_level = "中高"
        else:
            action = "觀望"
            reason = f"股價已在{analysis['position_desc']}，追高風險較大"
            risk_level = "高"
    elif trend == "下跌":
        if price_position > 60:
            action = "賣出或減碼"
            reason = f"股價雖處{analysis['position_desc']}但呈下跌趨勢，建議減碼"
            risk_level = "中高"
        elif price_position > 30:
            action = "觀望"
            reason = f"股價在{analysis['position_desc']}且下跌中，等待止跌訊號"
            risk_level = "中等"
        else:
            action = "觀望或小量買進"
            reason = f"股價已在{analysis['position_desc']}，可能接近短期支撐"
            risk_level = "中等"
    else:  # 平盤
        if price_position < 30:
            action = "可考慮買進"
            reason = f"股價在{analysis['position_desc']}，風險相對較低"
            risk_level = "中低"
        elif price_position > 70:
            action = "觀望"
            reason = f"股價在{analysis['position_desc']}，等待回檔"
            risk_level = "中等"
        else:
            action = "觀望"
            reason = "股價盤整中，等待明確趨勢"
            risk_level = "中等"

    return {
        'action': action,
        'reason': reason,
        'risk_level': risk_level,
        'market': market
    }


# ==================== 格式化輸出 ====================

def format_stock_markdown(stock_data: Dict[str, Any], include_analysis: bool = False) -> str:
    """將股票資料格式化為 Markdown（通用版本）"""
    try:
        market = stock_data.get('market', 'unknown')
        market_name = stock_data.get('market_name', '未知市場')
        code = stock_data.get('c', 'N/A')
        name = stock_data.get('n', stock_data.get('longName', 'N/A'))

        current = safe_float(stock_data.get('z'))
        prev_close = safe_float(stock_data.get('y'))
        open_price = stock_data.get('o', '-')
        high = stock_data.get('h', '-')
        low = stock_data.get('l', '-')
        volume = stock_data.get('v', '-')

        # 計算漲跌
        change, change_pct = calculate_price_change(current, prev_close)

        # 市場特定資訊
        extra_info = ""
        if market == 'us':
            currency = stock_data.get('currency', 'USD')
            exchange = stock_data.get('exchangeName', 'NYSE')
            extra_info = f"\n- **交易所**: {exchange}\n- **貨幣**: {currency}"

        markdown = f"""## 📊 {name} ({code})
### 🌏 市場: {market_name}

### 即時報價
- **成交價**: {current:.2f} ({change:+.2f}, {change_pct})
- **開盤**: {open_price}
- **最高**: {high}
- **最低**: {low}
- **昨收**: {prev_close:.2f}
- **成交量**: {volume} {'手' if market == 'us' else '張'}{extra_info}
"""

        # 買賣檔（僅台股和A股有詳細五檔）
        if market in ['taiwan', 'china']:
            bid_prices = stock_data.get('b', '').split('_')[:5]
            bid_volumes = stock_data.get('g', '').split('_')[:5]
            ask_prices = stock_data.get('a', '').split('_')[:5]
            ask_volumes = stock_data.get('f', '').split('_')[:5]

            markdown += "\n### 買賣五檔\n"
            markdown += "\n| 委買量 | 委買價 | 委賣價 | 委賣量 |\n"
            markdown += "|--------|--------|--------|--------|\n"

            for i in range(5):
                bid_vol = bid_volumes[i] if i < len(bid_volumes) else '-'
                bid_price = bid_prices[i] if i < len(bid_prices) else '-'
                ask_price = ask_prices[i] if i < len(ask_prices) else '-'
                ask_vol = ask_volumes[i] if i < len(ask_volumes) else '-'
                markdown += f"| {bid_vol} | {bid_price} | {ask_price} | {ask_vol} |\n"

        # 技術分析
        if include_analysis:
            analysis = analyze_technical_indicators(stock_data)
            suggestion = generate_trading_suggestion(analysis, stock_data)

            markdown += f"""
### 📈 技術分析
- **趨勢**: {analysis.get('trend', 'N/A')} ({analysis.get('trend_strength', 'N/A')})
- **價格位置**: {analysis.get('position_desc', 'N/A')} ({analysis.get('price_position', 0):.1f}%)

### 💡 交易建議
- **建議動作**: {suggestion['action']}
- **理由**: {suggestion['reason']}
- **風險等級**: {suggestion['risk_level']}

---
⚠️ **免責聲明**: 以上分析僅供參考，不構成投資建議。投資有風險，請謹慎評估。
"""

        return markdown

    except Exception as e:
        return f"❌ 格式化股票資料時發生錯誤: {str(e)}"


# ==================== MCP 工具 ====================

@mcp.tool(name="get_universal_stock_quote")
async def get_universal_stock_quote(params: UniversalStockQueryInput) -> str:
    """取得全球股票即時報價（支援台股、A股、美股）。

    此工具可查詢台灣、中國、美國三個市場的股票即時報價。
    系統會自動識別股票代碼所屬市場，也可手動指定市場類型。

    Args:
        params (UniversalStockQueryInput): 包含以下欄位:
            - stock_code (str): 股票代碼
            - market (str): 市場類型 ('taiwan', 'china', 'us', 'auto')
            - response_format (str): 輸出格式 ('markdown' 或 'json')

    Returns:
        str: 格式化的股票即時報價資訊

    Examples:
        查詢台積電: stock_code="2330", market="auto"
        查詢茅台: stock_code="600519", market="china"
        查詢蘋果: stock_code="AAPL", market="us"

    Note:
        - 台股資料約每5秒更新
        - A股和美股資料有延遲（約15分鐘）
        - 美股盤前盤後可能無數據
    """
    try:
        stock_info = await get_stock_quote(params.stock_code, params.market)

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(stock_info, ensure_ascii=False, indent=2)
        else:
            return format_stock_markdown(stock_info, include_analysis=False)

    except Exception as e:
        return f"❌ 查詢失敗: {str(e)}"


@mcp.tool(name="get_multiple_market_stocks_quotes")
async def get_multiple_market_stocks_quotes(params: MultiMarketStockQueryInput) -> str:
    """同時取得多支股票的即時報價（支援跨市場查詢）。

    此工具可一次查詢多支股票，支援混合不同市場的股票代碼。
    例如可同時查詢台積電、茅台、蘋果三支不同市場的股票。

    Args:
        params (MultiMarketStockQueryInput): 包含以下欄位:
            - stock_codes (List[str]): 股票代碼列表，最多20支
            - response_format (str): 輸出格式

    Returns:
        str: 所有股票的即時報價資訊

    Examples:
        跨市場查詢: stock_codes=["2330", "AAPL", "600519"]

    Note:
        - 自動識別每支股票所屬市場
        - 建議一次不超過10支以提升效能
    """
    try:
        results = []

        for code in params.stock_codes:
            try:
                stock_info = await get_stock_quote(code, MarketType.AUTO)
                results.append(stock_info)
                await asyncio.sleep(0.3)
            except Exception as e:
                results.append({
                    'c': code,
                    'error': str(e)
                })

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(results, ensure_ascii=False, indent=2)
        else:
            markdown = "# 📊 多市場股票即時報價\n\n"
            for stock in results:
                if 'error' in stock:
                    markdown += f"## ❌ {stock['c']}: {stock['error']}\n\n"
                else:
                    markdown += format_stock_markdown(stock, include_analysis=False)
                    markdown += "\n---\n\n"

            return markdown

    except Exception as e:
        return f"❌ 查詢失敗: {str(e)}"


@mcp.tool(name="analyze_universal_stock")
async def analyze_universal_stock(params: UniversalTechnicalAnalysisInput) -> str:
    """進行全球股票技術分析並提供買賣建議（支援台股、A股、美股）。

    此工具結合即時報價進行技術面分析，提供趨勢判斷、價格位置分析
    和交易建議，適用於所有支援的市場。

    Args:
        params (UniversalTechnicalAnalysisInput): 包含以下欄位:
            - stock_code (str): 股票代碼
            - market (str): 市場類型
            - analysis_type (str): 分析類型 ('basic', 'advanced', 'full')
            - response_format (str): 輸出格式

    Returns:
        str: 包含技術分析和交易建議的完整報告

    Examples:
        分析台積電: stock_code="2330", market="auto"
        分析特斯拉: stock_code="TSLA", market="us", analysis_type="full"

    Warning:
        分析結果僅供參考，不構成實際投資建議。
    """
    try:
        stock_info = await get_stock_quote(params.stock_code, params.market)

        if params.response_format == ResponseFormat.JSON:
            analysis = analyze_technical_indicators(stock_info)
            suggestion = generate_trading_suggestion(analysis, stock_info)
            result = {
                'stock_info': stock_info,
                'technical_analysis': analysis,
                'trading_suggestion': suggestion
            }
            return json.dumps(result, ensure_ascii=False, indent=2)
        else:
            return format_stock_markdown(stock_info, include_analysis=True)

    except Exception as e:
        return f"❌ 分析失敗: {str(e)}"


@mcp.tool(name="compare_multi_market_stocks")
async def compare_multi_market_stocks(params: MultiMarketStockQueryInput) -> str:
    """比較多支股票的表現（支援跨市場比較）。

    此工具可同時分析多支股票，支援跨市場比較。
    例如可比較台積電、三星、Intel 等不同國家的半導體股。

    Args:
        params (MultiMarketStockQueryInput): 包含股票代碼列表和輸出格式

    Returns:
        str: 股票比較分析結果

    Examples:
        跨市場比較: stock_codes=["2330", "NVDA", "005930"]
    """
    try:
        comparisons = []

        for code in params.stock_codes:
            try:
                stock_info = await get_stock_quote(code, MarketType.AUTO)
                analysis = analyze_technical_indicators(stock_info)
                suggestion = generate_trading_suggestion(analysis, stock_info)

                current = safe_float(stock_info.get('z'))
                prev_close = safe_float(stock_info.get('y'))
                change, change_pct = calculate_price_change(current, prev_close)

                comparisons.append({
                    'code': code,
                    'name': stock_info.get('n', stock_info.get('longName', 'N/A')),
                    'market': stock_info.get('market_name', 'N/A'),
                    'price': current,
                    'change': change_pct,
                    'trend': analysis.get('trend', 'N/A'),
                    'position': f"{analysis.get('price_position', 0):.1f}%",
                    'suggestion': suggestion['action'],
                    'risk': suggestion['risk_level']
                })

                await asyncio.sleep(0.3)

            except Exception as e:
                comparisons.append({
                    'code': code,
                    'error': str(e)
                })

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(comparisons, ensure_ascii=False, indent=2)
        else:
            markdown = "# 📊 多市場股票比較分析\n\n"
            markdown += "| 代碼 | 名稱 | 市場 | 現價 | 漲跌幅 | 趨勢 | 位置 | 建議 | 風險 |\n"
            markdown += "|------|------|------|------|--------|------|------|------|------|\n"

            for comp in comparisons:
                if 'error' in comp:
                    markdown += f"| {comp['code']} | - | - | - | - | ❌ {comp['error']} | - | - | - |\n"
                else:
                    markdown += f"| {comp['code']} | {comp['name']} | {comp['market']} | {comp['price']:.2f} | {comp['change']} | {comp['trend']} | {comp['position']} | {comp['suggestion']} | {comp['risk']} |\n"

            markdown += "\n---\n⚠️ **免責聲明**: 以上分析僅供參考，不構成投資建議。"

            return markdown

    except Exception as e:
        return f"❌ 比較分析失敗: {str(e)}"


# ==================== 主程式 ====================

def main():
    """主程式入口點"""
    # FastMCP 的 run() 方法會自動管理 event loop
    # 在 Claude Desktop 環境中使用 STDIO 傳輸協定（預設）
    # 每個 MCP 伺服器進程有獨立的 event loop，不會衝突
    mcp.run()

if __name__ == "__main__":
    main()
