import java.util.List;
import java.util.stream.IntStream;

public final class Streams {
    static long run(int seed) {
        long total = 0;
        for (int round = 0; round < 16; round++) {
            total += IntStream.range(0, 8192)
                    .map(x -> (x * 31 + seed) ^ (x >>> 3))
                    .filter(x -> (x & 3) != 0)
                    .asLongStream()
                    .sum();
            List<String> strings = IntStream.range(0, 256).mapToObj(x -> "v" + (x + seed)).toList();
            total += strings.stream().map(String::length).reduce(0, Integer::sum);
        }
        return total;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 3500, Streams::run);
    }
}
