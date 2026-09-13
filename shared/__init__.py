"""Code used by both halves of the project (detector and server).

Nothing in here may import a third-party package: the detector ships it inside
a PyInstaller binary and the server imports the very same file, so it has to
stay dependency-free and cheap to import.
"""
