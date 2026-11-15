(define (problem rebuild-16)
  (:domain blocks-world)
  (:objects a b c d e f - block)
  (:init
    (clear a) (clear b) (clear c) (clear d) (hand-empty) (on d f) (on f e) (on-table a) (on-table b) (on-table c) (on-table e))
  (:goal (and (on a e) (on b c) (on f b) (on-table c) (on-table d) (on-table e)))
)
