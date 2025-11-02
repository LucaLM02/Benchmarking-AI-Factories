import argparse
from benchmark_manager import BenchmarkManager

def main():
    parser = argparse.ArgumentParser(description="Benchmark Manager CLI")
    parser.add_argument("--load", type=str, help="Path to the benchmark recipe YAML")
    parser.add_argument("--workspace", type=str, default=None, help="Override workspace path defined in recipe")
    parser.add_argument("--run", action="store_true", help="Run the benchmark")

    args = parser.parse_args()

    manager = BenchmarkManager()

    if args.load:
        manager.load_recipe(args.load, override_workspace=args.workspace)

    if args.workspace:
        manager.override_workspace(args.workspace)

    if args.run:
        manager.run_benchmark()

if __name__ == "__main__":
    main()
