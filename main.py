import sys
from verifier import check_access

if not check_access():
    print("Access denied.")
    exit()

import MAYA_12_10_25
