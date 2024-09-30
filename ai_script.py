import os

def gather_code_from_directory(directory, output_file, ignore_dirs=None):
    if ignore_dirs is None:
        ignore_dirs = {'.venv', 'postgres_data'}

    with open(output_file, 'w', encoding='utf-8') as outfile:
        for root, dirs, files in os.walk(directory):
            # Игнорируем указанные директории
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    with open(file_path, 'r', encoding='utf-8') as infile:
                        outfile.write(f"\n# Code from {file}\n")
                        outfile.write(infile.read())
                        outfile.write(f"\n# End of code from {file}\n")

if __name__ == "__main__":
    project_directory = os.getcwd()  # Текущая рабочая директория
    output_file_path = "combined_code.py"  # Имя выходного файла

    gather_code_from_directory(project_directory, output_file_path)
    print(f"Code from all .py files in {project_directory} has been combined into {output_file_path}")
