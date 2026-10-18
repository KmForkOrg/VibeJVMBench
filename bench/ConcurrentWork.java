import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

public final class ConcurrentWork {
    static final class Counter {
        long value;

        synchronized void add(long v) {
            value += v;
        }
    }

    static long run(int seed) throws InterruptedException {
        AtomicLong atomic = new AtomicLong();
        Counter locked = new Counter();
        ConcurrentHashMap<Integer, Long> map = new ConcurrentHashMap<>();
        long[] sums = new long[4];
        Thread[] threads = new Thread[4];
        for (int t = 0; t < threads.length; t++) {
            int id = t;
            threads[t] = new Thread(() -> {
                long sum = 0;
                for (int i = 0; i < 4096; i++) {
                    long v = i + seed;
                    atomic.addAndGet(v);
                    locked.add(v);
                    int key = id * 256 + (i & 255);
                    map.put(key, v);
                    sum += map.get(key);
                }
                sums[id] = sum;
            });
            threads[t].start();
        }
        for (Thread thread : threads) {
            thread.join();
        }
        long total = atomic.get() + locked.value;
        for (long sum : sums) {
            total += sum;
        }
        for (int i = 0; i < 1024; i++) {
            total += map.get(i);
        }
        return total;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 469, ConcurrentWork::run);
    }
}
