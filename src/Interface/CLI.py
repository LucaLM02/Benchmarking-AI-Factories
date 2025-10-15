import argparse
from benchmark_manager import BenchmarkManager

def main():
    parser = argparse.ArgumentParser(description="AI Factory Benchmark CLI")
    parser.add_argument("--load", type=str, help="Load a benchmark recipe YAML file")
    parser.add_argument("--show", action="store_true", help="Show summary of loaded recipe")
    parser.add_argument("--run", action="store_true", help="Run the benchmark")
    
    args = parser.parse_args()
    
    manager = BenchmarkManager()
    
    if args.load:
        manager.load_recipe(args.load)
    
    if args.show:
        manager.show_summary()
    
    if args.run:
        manager.run_benchmark()

if __name__ == "__main__":
    main()
