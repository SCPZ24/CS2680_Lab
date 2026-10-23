import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="madsLoop coding agent")
    parser.add_argument("-p", required=True, help="需要解决的问题描述")
    parser.add_argument("--logs", action="store_true", help="开启运行日志")
    args = parser.parse_args()

    messages = []

    while True:
        pass
