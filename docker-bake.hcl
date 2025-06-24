group "default" {
  targets = ["vllm", "sglang"]
}

target "vllm" {
  context = "."
  dockerfile = "Dockerfile"
  args = {
    ROLLOUT_ENGINE = "vllm"
    REPO = "248189905876.dkr.ecr.us-east-1.amazonaws.com/greenland"
    BASE_TAG = "base"
  }
  tags = ["248189905876.dkr.ecr.us-east-1.amazonaws.com/greenland:verl_vllm"]
  platforms = ["linux/amd64"]
  cache-to = ["type=registry,ref=248189905876.dkr.ecr.us-east-1.amazonaws.com/greenland:cache,mode=max"]
  cache-from = ["type=registry,ref=248189905876.dkr.ecr.us-east-1.amazonaws.com/greenland:cache"]
  push = true
}

target "sglang" {
  inherits = ["vllm"]
  args = {
    ROLLOUT_ENGINE = "sglang"
  }
  tags = ["248189905876.dkr.ecr.us-east-1.amazonaws.com/greenland:verl_sglang"]
}
