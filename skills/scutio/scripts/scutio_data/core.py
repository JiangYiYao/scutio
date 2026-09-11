"""公开的证券代码与结果信封契约；不初始化数据源连接。"""

from scutio_data._runtime.results import envelope_items as envelope_items
from scutio_data._runtime.results import result_err as result_err
from scutio_data._runtime.results import result_list as result_list
from scutio_data._runtime.results import result_list_err as result_list_err
from scutio_data._runtime.results import result_ok as result_ok
from scutio_data._runtime.symbols import canonical_symbol as canonical_symbol
from scutio_data._runtime.symbols import get_prefix as get_prefix
from scutio_data._runtime.symbols import market_of as market_of
from scutio_data._runtime.symbols import normalize_code as normalize_code
from scutio_data._runtime.symbols import require_market as require_market
from scutio_data._runtime.symbols import split_code as split_code
from scutio_data._runtime.timeouts import request_timeout as request_timeout

__all__ = [
    "split_code",
    "get_prefix",
    "normalize_code",
    "market_of",
    "canonical_symbol",
    "result_ok",
    "result_err",
    "result_list",
    "result_list_err",
    "envelope_items",
    "require_market",
    "request_timeout",
]
