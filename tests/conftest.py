import sys
from pathlib import Path

# 让测试可以 import 仓库根目录的 audit.py(容器内同样适用:/app)。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
