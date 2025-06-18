import os
import shutil
import PyInstaller.__main__

# --- Configuration ---
APP_NAME = "OrderManagementSystem"
SCRIPT_FILE = "app.py"
DATA_FOLDER = "data"
TEMPLATES_FOLDER = "templates"
ASSETS_FOLDER = "assets"

# --- Build Process ---
def build():
    """Builds the executable using PyInstaller."""
    print("Starting build process...")

    # Define PyInstaller arguments
    # --onefile: Bundle everything into a single .exe
    # --windowed: Run without a console window
    # --add-data: Include data and template files
    pyinstaller_args = [
        '--name=%s' % APP_NAME,
        '--onefile',
        '--windowed',
        '--add-data=%s%s%s' % (ASSETS_FOLDER, os.pathsep, ASSETS_FOLDER),
        '--add-data=%s%s%s' % (TEMPLATES_FOLDER, os.pathsep, TEMPLATES_FOLDER),
        '--add-data=%s%s%s' % (DATA_FOLDER, os.pathsep, DATA_FOLDER),
        os.path.join(os.getcwd(), SCRIPT_FILE),
    ]

    print(f"Running PyInstaller with args: {' '.join(pyinstaller_args)}")

    # Execute PyInstaller
    PyInstaller.__main__.run(pyinstaller_args)

    print("\nCleaning up build files...")
    # Clean up temporary files created by PyInstaller
    shutil.rmtree('build')
    os.remove(f'{APP_NAME}.spec')

    print(f"\nBuild complete! Find your application at dist/{APP_NAME}.exe")

if __name__ == '__main__':
    build()
