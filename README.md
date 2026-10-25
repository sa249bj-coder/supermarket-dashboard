# スーパー価格比較 自動更新版（実装ひな形）

- `index.html`: スマホ向け検索・お買い得比較画面。HTTPS公開時は同じ場所の `prices.json` を自動読込し、30分ごとに再取得。
- `collect.py`: `sources.json` に設定した公開HTTPSの**構造化JSONフィード**から価格を取得・検証・登録。
- `.github/workflows/update.yml`: GitHub Actionsで日本時間9:15/15:15頃に定期実行（遅延あり）。
- `collection_status.json`: 取得状況。出典や商品名・税込価格のない行は登録しない。

## 現在の制限

**実在スーパーの自動取得元はまだ接続していません。** `sources.json` は空で、実価格も未登録です。スーパーのチラシ画像やWebページをこのスクリプトが自動解読するわけではありません。各店の利用規約・robots設定と、取得方法を確認したうえで店舗ごとのアダプターが必要です。

## 公開方法

GitHubアカウントと公開リポジトリ、GitHub Pagesの設定が必要です。Pagesの公開元をルートにすると同じURLを2人で閲覧できます。価格データの閲覧は共有できますが、買い物リストのリアルタイム同期機能は未実装です。

## フィードの登録

`sources.json` の `sources` に `{ "name": "提供元", "url": "https://.../prices.json" }` を追加します。フィードの `prices` 配列は `name,store_id,price_yen,source_url,checked_at` が必須です。`product_id,brand,size,sale,valid_until` を推奨します。フィードが未確認ならURLを追加しないでください。
