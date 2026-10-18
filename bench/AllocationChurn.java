public final class AllocationChurn {
    record Pair(long x, long y) {}

    record Node(Pair value, Node next) {}

    static long pair(int i, int seed) {
        Pair p = new Pair(i + seed, i * 3L);
        return p.x() ^ p.y();
    }

    static long run(int seed) {
        long sum = 0;
        for (int batch = 0; batch < 32; batch++) {
            Node head = null;
            for (int i = 0; i < 1024; i++) {
                sum += pair(i, seed);
                head = new Node(new Pair(i + seed + batch, sum), head);
            }
            for (Node node = head; node != null; node = node.next()) {
                sum += node.value().x() ^ node.value().y();
            }
        }
        return sum;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 5702, AllocationChurn::run);
    }
}
