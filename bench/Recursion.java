public final class Recursion {
    record Node(int value, Node left, Node right) {}

    static Node tree(int depth, int seed) {
        if (depth == 0) {
            return null;
        }
        return new Node(seed, tree(depth - 1, seed * 3 + 1), tree(depth - 1, seed * 3 + 2));
    }

    static long walk(Node n) {
        return n == null ? 0 : n.value() + walk(n.left()) + walk(n.right());
    }

    static int fib(int n) {
        return n < 2 ? n : fib(n - 1) + fib(n - 2);
    }

    static long run(int seed) {
        Node root = tree(11, seed);
        long sum = fib(25 + (seed & 1));
        for (int i = 0; i < 32; i++) {
            sum += walk(root);
        }
        return sum;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 2581, Recursion::run);
    }
}
