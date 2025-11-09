{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  # Make python and pip available in the shell
  buildInputs = [
    pkgs.python3
    # Add system-level dependencies here if needed (e.g., pkgs.libjpeg, pkgs.zlib)
    (pkgs.python3.withPackages (ps: with ps; [
      pip
      virtualenv
      # pyqt5
      # Add other python packages here as needed (e.g., numpy)
    ]))
    # Include the full Qt5 suite for all necessary plugins and tools
    # pkgs.qt5.full
#     pkgs.qt5.qtbase
#     pkgs.qt5.qtsvg
  ];

  # Commands to run when entering the shell
  shellHook = ''
#     export QT_QPA_PLATFORM_PLUGIN_PATH=${pkgs.qt5.qtbase.bin}/lib/qt-${pkgs.qt5.qtbase.version}/plugins
#     echo "PyQt5 development environment ready. Run 'python your_script.py' to test."
    # Create a virtual environment if it doesn't exist
    if [ ! -d ".venv" ]; then
      echo "Creating virtual environment..."
      python3 -m venv .venv
    fi
    # Activate the virtual environment
    source .venv/bin/activate
    # Install packages from requirements.txt
    pip install -r requirements.txt
    echo "Python environment is ready and activated."
    export SHELL=/usr/bin/bash
    echo "SHELL variable set to $SHELL for host compatibility."
  '';
}
