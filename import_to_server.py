from neo4j import GraphDatabase
from neo4j_backup import Importer

if __name__ == "__main__":
    # ========== 请修改为外部服务器的连接信息 ==========
    uri = "bolt://172.1.2.232:7687"       # 例如 bolt://192.168.1.100:7687
    username = "neo4j"                  # 外部服务器的用户名
    password = "Sunyard000"        # 替换为真实密码
    database = "neo4j"                  # 目标数据库名，通常是 neo4j
    # ===============================================

    encrypted = False  # 本地或内网连接一般填 False

    # 建立驱动连接
    driver = GraphDatabase.driver(uri, auth=(username, password), encrypted=encrypted)

    # 指定 data_dump 文件夹路径（如果脚本与 data_dump 同级，直接用 "./data_dump"）
    project_dir = "./data_dump"

    # 初始化导入器（移除不支持的参数）
    importer = Importer(
        project_dir=project_dir,
        driver=driver,
        database=database,
        input_yes=True          # 自动确认覆盖操作
    )

    # 执行导入
    importer.import_data()

    print("✅ 数据导入完成！")