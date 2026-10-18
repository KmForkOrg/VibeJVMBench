import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.Method;

public final class RuntimeKernels {
    static final class PayloadLoader extends ClassLoader {
        PayloadLoader(ClassLoader parent) {
            super(parent);
        }

        @Override
        protected Class<?> findClass(String name) throws ClassNotFoundException {
            try (InputStream in = RuntimeKernels.class.getResourceAsStream("/payload/" + name + ".bin")) {
                if (in == null) {
                    throw new ClassNotFoundException(name);
                }
                byte[] bytes = in.readAllBytes();
                return defineClass(name, bytes, 0, bytes.length);
            } catch (IOException e) {
                throw new ClassNotFoundException(name, e);
            }
        }
    }

    public static void main(String[] args) throws Exception {
        ClassLoader loader = new PayloadLoader(RuntimeKernels.class.getClassLoader());
        Method run = loader.loadClass("Kernels").getMethod("run", int.class);
        Bench.measure(args, 200, seed -> (long) run.invoke(null, 1 + (seed & 7)));
    }
}
