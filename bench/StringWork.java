public final class StringWork {
    static long run(int seed) {
        long sum = 0;
        for (int i = 0; i < 8192; i++) {
            int value = i * 37 + seed;
            String text = new StringBuilder(40)
                    .append("entity:")
                    .append(value)
                    .append(':')
                    .append(value ^ 0x55aa)
                    .toString();
            int colon = text.lastIndexOf(':');
            sum += Integer.parseInt(text.substring(7, colon));
            sum += Integer.parseInt(text.substring(colon + 1));
            sum = sum * 31 + text.hashCode();
        }
        return sum;
    }

    public static void main(String[] args) throws Exception {
        Bench.measure(args, 2131, StringWork::run);
    }
}
