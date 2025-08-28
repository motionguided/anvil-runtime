import threading
from collections import Counter
from time import time

counts = Counter()

lock = threading.RLock()

def count(desc, amt):
    with lock:
        counts[desc] += amt

last_count = Counter()
last_time = 0
start_time = time()

def print_stats():
    global last_count, last_time

    if last_time == 0:
        last_time = time()
        return

    with lock:
        print("\n\n\n\n")
        print("********************")
        print("********************")
        print("COUNTS in {:.0f} seconds".format(time()-last_time))
        for desc, c in sorted((counts-last_count).items(), key=lambda x: x[0]):
            print("{:>48}: {:,}".format(desc, c))
        print("COUNTS TOTAL ({:.0f} seconds since boot)".format(time() - start_time))
        for desc, c in sorted((counts).items(), key=lambda x: x[0]):
            print("{:>48}: {:,}".format(desc, c))

        print("********************")
        print("\n\n\n\n")

        last_count = Counter(counts)
        last_time = time()
