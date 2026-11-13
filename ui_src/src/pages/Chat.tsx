// Phase-2 Wave 7 measurement scaffolding (work-order step 6, commit B).
// ONE Bubble + ONE Sender: the minimal import surface that makes
// rolldown emit the Chat chunk so the build can measure the X weight.
// Step 7 replaces this file with the real page.
import { Bubble, Sender } from "@ant-design/x";

export default function Chat() {
  return (
    <section className="ui-chat">
      <Bubble content="Chat lands in Wave 7." />
      <Sender placeholder="Ask the corpus" />
    </section>
  );
}
