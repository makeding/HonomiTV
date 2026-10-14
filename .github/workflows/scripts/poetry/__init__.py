# 旧バージョンの KonomiTV インストーラー向けの Poetry 互換モジュール
# サードパーティーライブラリ内の Python の site-packages に poetry パッケージとして配置される
## KonomiTV は依存パッケージの管理を Poetry から uv に移行したが、旧バージョンのインストーラーで「開発版をインストール」
## 「開発版へアップデート」を選ぶと、最新のサードパーティーライブラリ内の Python で `python -m poetry ...` が実行される
## このモジュールは、旧バージョンのインストーラーが実行する次の Poetry コマンドだけを uv の操作に読み替えて互換性を保つ
## - poetry env use <Python のパス>: uv sync が仮想環境を作成するため、何もせずに成功を返す
## - poetry install --only main --no-root: uv sync --frozen --no-dev に読み替える
## - poetry run python KonomiTV-Service.py ...: 仮想環境の Python で KonomiTV-Service.py を直接実行する (v0.13.0 以前の Windows 版)
## この互換モジュールの所有者はインストーラー更新経路であり、v0.14.1 未満のインストーラーがサポート対象外になった時点、遅くとも 2027-04-14 までに削除する
## build_thirdparty.yaml がこのモジュールを thirdparty/Python の site-packages へ配置し、python -m poetry --version が実行できることを回帰確認する
