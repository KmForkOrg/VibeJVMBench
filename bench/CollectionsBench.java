import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class CollectionsBench {
    static final class Ascending implements Comparator<Integer> {
        static final Ascending INSTANCE = new Ascending();

        @Override
        public int compare(Integer a, Integer b) {
            return Integer.compare(a, b);
        }
    }

    static final class Descending implements Comparator<Integer> {
        static final Descending INSTANCE = new Descending();

        @Override
        public int compare(Integer a, Integer b) {
            return Integer.compare(b, a);
        }
    }

    static final class LowBits implements Comparator<Integer> {
        static final LowBits INSTANCE = new LowBits();

        @Override
        public int compare(Integer a, Integer b) {
            int c = Integer.compare(a & 255, b & 255);
            return c != 0 ? c : Integer.compare(a, b);
        }
    }

    static final List<Comparator<Integer>> COMPARATORS =
            List.of(Ascending.INSTANCE, Descending.INSTANCE, LowBits.INSTANCE);

    static long run(int seed) {
        List<Integer> list = new ArrayList<>();
        Map<Integer, Integer> ints = new HashMap<>();
        Map<String, Integer> strings = new HashMap<>();
        for (int i = 0; i < 2048; i++) {
            int v = (i * 7919 + seed) & 65535;
            list.add(v);
            ints.put(i, v);
            strings.put("key" + i, v);
        }
        long sum = 0;
        for (int i = 0; i < 2048; i++) {
            sum += list.get(i) + ints.get(i) + strings.get("key" + i);
        }
        for (Comparator<Integer> comparator : COMPARATORS) {
            list.sort(comparator);
            for (int i = 0; i < list.size(); i++) {
                sum = sum * 31 + list.get(i);
            }
        }
        return sum;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 3200, CollectionsBench::run);
    }
}
